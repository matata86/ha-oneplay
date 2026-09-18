"""Klient Oneplay API (http.cms.jyxo.cz).

Protokol převzatý z Kodi doplňku waladir/plugin.video.oneplay: každé volání
otevře websocket, z něj vezme serverId, pošle POST a u stavu OkAsync čeká
na odpověď se stejným requestId ve websocketu.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

API_URL = "https://http.cms.jyxo.cz/api/"
WS_URL = "wss://ws.cms.jyxo.cz/websocket/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0"
APP_VERSION = "R11.33"
BASE_VERSION = 11
DEVICE_NAME = "Home Assistant"
CW_TITLE = "Pokračovat ve sledování"
CW_CAROUSEL = "page:1;carousel:5"


class OneplayError(Exception):
    """Chyba volání API (Oneplay vrátil jiný stav než Ok)."""


class OneplayAuthError(OneplayError):
    """Přihlášení selhalo (špatné údaje)."""


class OneplayApi:
    def __init__(self, session: aiohttp.ClientSession, email: str, password: str,
                 account_id: str | None = None, profile_id: str | None = None) -> None:
        self._session = session
        self._email = email
        self._password = password
        self.account_id = account_id
        self.profile_id = profile_id
        self._token: str | None = None
        self._version = BASE_VERSION
        self._device_id: str | None = None

    # --- nízká úroveň ---------------------------------------------------------
    async def _raw(self, method: str, payload: dict | None, auth: bool = True) -> dict:
        for _ in range(40):
            try:
                return await self._raw_once(method, payload, auth)
            except _VersionGone:
                self._version += 1
                _LOGGER.info("Oneplay API: zkouším verzi v1.%02d", self._version)
        raise OneplayError("Nenalezena funkční verze API")

    async def _raw_once(self, method: str, payload: dict | None, auth: bool) -> dict:
        client_id = str(uuid.uuid4())
        request_id = str(uuid.uuid4())
        timeout = aiohttp.ClientTimeout(total=25)
        async with self._session.ws_connect(WS_URL + client_id, timeout=10) as ws:
            init = await ws.receive_json(timeout=10)
            server_id = init["data"]["serverId"]
            post: dict[str, Any] = {
                "deviceInfo": {"deviceType": "web", "appVersion": APP_VERSION,
                               "deviceManufacturer": "Unknown", "deviceOs": "Linux"},
                "capabilities": {"async": "websockets"},
                "context": {"requestId": request_id, "clientId": client_id,
                            "sessionId": server_id, "serverId": server_id},
            }
            if payload:
                post.update(payload)
            headers = {"User-Agent": USER_AGENT, "Accept": "*/*",
                       "Content-type": "application/json;charset=UTF-8"}
            if auth and self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            url = f"{API_URL}v1.{self._version:02d}/{method}"
            async with self._session.post(url, data=json.dumps(post), headers=headers,
                                          timeout=timeout) as resp:
                if resp.status == 404:
                    raise _VersionGone
                body = json.loads(await resp.read() or b"{}")
            if body.get("result", {}).get("status") == "OkAsync":
                while True:
                    msg = await ws.receive_json(timeout=20)
                    resp_ = msg.get("response") or {}
                    if resp_.get("context", {}).get("requestId") == request_id:
                        body = resp_
                        break
        result = body.get("result") or {}
        if result.get("status") != "Ok":
            raise OneplayError(result.get("message") or f"{method}: {result.get('status')}")
        return body.get("data") or {}

    # --- přihlášení -----------------------------------------------------------
    async def login_credentials(self) -> dict:
        """První krok přihlášení. Vrací step (buď s tokenem, nebo výběr účtu)."""
        try:
            data = await self._raw("user.login.step", {"payload": {"command": {
                "schema": "LoginWithCredentialsCommand",
                "email": self._email, "password": self._password}}}, auth=False)
        except OneplayError as err:
            raise OneplayAuthError(str(err)) from err
        return data.get("step") or {}

    @staticmethod
    def accounts_from_step(step: dict) -> list[tuple[str, str]]:
        """[(popis, accountId)] z kroku ShowAccountChooserStep."""
        out = []
        for group in step.get("groups", []):
            for acc in group.get("accounts", []):
                if acc.get("extId") or acc.get("isActive"):
                    popis = f"{acc.get('name')} ({acc.get('accountProvider', '')})".strip()
                    out.append((popis, str(acc["accountId"])))
        return out

    async def login(self) -> None:
        """Celé přihlášení: údaje → účet → pojmenování zařízení → profil."""
        step = await self.login_credentials()
        if step.get("schema") == "ShowAccountChooserStep":
            ucty = self.accounts_from_step(step)
            if not ucty:
                raise OneplayAuthError("Účet nemá žádný aktivní účet Oneplay")
            account = self.account_id if self.account_id in {u[1] for u in ucty} else ucty[0][1]
            data = await self._raw("user.login.step", {"payload": {"command": {
                "schema": "LoginWithAccountCommand", "accountId": account,
                "authCode": step["authToken"]}}}, auth=False)
            step = data.get("step") or {}
        self._token = step.get("bearerToken")
        if not self._token:
            raise OneplayAuthError("Oneplay nevrátil token")
        self._device_id = ((step.get("currentUser") or {}).get("currentDevice") or {}).get("id")
        await self._tidy_devices()
        if not self.profile_id:
            profily = await self.profiles()
            self.profile_id = profily[0][1] if profily else None
        if self.profile_id:
            data = await self._raw("user.profile.select", {"payload": {"profileId": self.profile_id}})
            if data.get("bearerToken"):
                self._token = data["bearerToken"]

    async def _tidy_devices(self) -> None:
        """Pojmenuje vlastní zařízení „Home Assistant“ a smaže starší se stejným jménem,
        aby se na účtu nehromadila zařízení po každém přihlášení."""
        if not self._device_id:
            return
        try:
            await self._raw("user.device.change",
                            {"payload": {"id": self._device_id, "name": DEVICE_NAME}})
            for dev in await self.devices():
                if dev.get("id") != self._device_id and dev.get("name") == DEVICE_NAME:
                    await self._raw("user.device.remove", {"payload": {"criteria": {
                        "schema": "UserDeviceIdCriteria", "id": dev["id"]}}})
        except OneplayError as err:
            _LOGGER.debug("Úklid zařízení se nepovedl: %s", err)

    async def profiles(self) -> list[tuple[str, str]]:
        data = await self._raw("user.profiles.display", {"payload": {"mode": "change"}})
        return [(p["profile"]["name"], str(p["profile"]["id"]))
                for p in data.get("availableProfiles", {}).get("profiles", [])]

    # --- data -------------------------------------------------------------------
    async def call(self, method: str, payload: dict | None) -> dict:
        """Volání s tokenem. Při chybě jednou zkusí nové přihlášení."""
        if not self._token:
            await self.login()
        try:
            return await self._raw(method, payload)
        except OneplayError as err:
            _LOGGER.debug("Oneplay %s selhalo (%s), přihlašuji znovu", method, err)
            await self.login()
            return await self._raw(method, payload)

    async def devices(self) -> list[dict]:
        data = await self._raw("setting.display", {"payload": {"screen": "devices"}})
        for block in (data.get("screen") or {}).get("blocks") or []:
            if block.get("schema") == "SettingUserDevicesBlock":
                return (block.get("devices") or {}).get("devices") or []
        return []

    async def devices_checked(self) -> list[dict]:
        if not self._token:
            await self.login()
        try:
            return await self.devices()
        except OneplayError:
            await self.login()
            return await self.devices()

    async def continue_watching(self) -> list[dict]:
        """Dlaždice řady „Pokračovat ve sledování“, nejnověji sledované první."""
        try:
            data = await self.call("carousel.display", {"payload": {
                "carouselId": CW_CAROUSEL, "paging": {"count": 6, "position": 1}}})
            tiles = (data.get("carousel") or {}).get("tiles")
            if tiles is not None:
                return tiles
        except OneplayError as err:
            _LOGGER.debug("carousel.display selhalo (%s), beru celou úvodní stránku", err)
        # Záloha: celá úvodní stránka a hledání řady podle názvu (id se může změnit)
        data = await self.call("page.category.display", {"payload": {"categoryId": "1"}})
        for block in (data.get("layout") or {}).get("blocks") or []:
            for car in block.get("carousels") or []:
                if (car.get("tracking") or {}).get("title") == CW_TITLE or car.get("id") == CW_CAROUSEL:
                    return car.get("tiles") or []
        return []


class _VersionGone(Exception):
    """HTTP 404 — Oneplay přešel na novější verzi API."""
