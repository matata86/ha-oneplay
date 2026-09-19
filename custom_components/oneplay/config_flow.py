"""Config flow: e-mail + heslo, případně výběr účtu a profilu."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DEVICE_NAME, OneplayApi, OneplayAuthError, OneplayError
from .const import (CONF_ACCOUNT_ID, CONF_DEVICE_ID, CONF_PROFILE_ID, CONF_TV_ENTITY, CONF_TV_SOURCE,
                    DEFAULT_TV_ENTITY, DEFAULT_TV_SOURCE, DOMAIN)

_LOGGER = logging.getLogger(__name__)


def _select(options: list[tuple[str, str]]) -> selector.SelectSelector:
    return selector.SelectSelector(selector.SelectSelectorConfig(
        options=[selector.SelectOptionDict(value=v, label=l) for l, v in options],
        mode=selector.SelectSelectorMode.LIST))


class OneplayConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return OneplayOptionsFlow()

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._accounts: list[tuple[str, str]] = []
        self._profiles: list[tuple[str, str]] = []

    def _api(self) -> OneplayApi:
        return OneplayApi(async_get_clientsession(self.hass), self._data[CONF_EMAIL],
                          self._data[CONF_PASSWORD], self._data.get(CONF_ACCOUNT_ID),
                          self._data.get(CONF_PROFILE_ID))

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_EMAIL].strip().lower())
            self._abort_if_unique_id_configured()
            self._data = {**user_input, CONF_EMAIL: user_input[CONF_EMAIL].strip()}
            try:
                step = await self._api().login_credentials()
            except OneplayAuthError:
                errors["base"] = "invalid_auth"
            except (OneplayError, Exception):  # noqa: BLE001 — síť, timeout
                _LOGGER.exception("Oneplay: chyba při přihlášení")
                errors["base"] = "cannot_connect"
            else:
                if step.get("schema") == "ShowAccountChooserStep":
                    self._accounts = OneplayApi.accounts_from_step(step)
                    if len(self._accounts) > 1:
                        return await self.async_step_account()
                    if self._accounts:
                        self._data[CONF_ACCOUNT_ID] = self._accounts[0][1]
                return await self.async_step_profile()

        schema = vol.Schema({
            vol.Required(CONF_EMAIL): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL)),
            vol.Required(CONF_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
            vol.Required(CONF_TV_ENTITY, default=DEFAULT_TV_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="media_player")),
            vol.Required(CONF_TV_SOURCE, default=DEFAULT_TV_SOURCE): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_account(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data[CONF_ACCOUNT_ID] = user_input[CONF_ACCOUNT_ID]
            return await self.async_step_profile()
        return self.async_show_form(step_id="account", data_schema=vol.Schema({
            vol.Required(CONF_ACCOUNT_ID): _select(self._accounts)}))

    async def async_step_profile(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data[CONF_PROFILE_ID] = user_input[CONF_PROFILE_ID]
            return self._create()
        if not self._profiles:
            api = self._api()
            try:
                await api.login()
                self._profiles = await api.profiles()
            except OneplayAuthError:
                errors["base"] = "invalid_auth"
            except (OneplayError, Exception):  # noqa: BLE001
                _LOGGER.exception("Oneplay: chyba při načtení profilů")
                errors["base"] = "cannot_connect"
            if not errors and len(self._profiles) <= 1:
                if self._profiles:
                    self._data[CONF_PROFILE_ID] = self._profiles[0][1]
                return self._create()
        if errors:
            return self.async_show_form(step_id="user", errors=errors)
        return self.async_show_form(step_id="profile", data_schema=vol.Schema({
            vol.Required(CONF_PROFILE_ID): _select(self._profiles)}))

    def _create(self) -> ConfigFlowResult:
        return self.async_create_entry(title=self._data[CONF_EMAIL], data=self._data)


class OneplayOptionsFlow(OptionsFlow):
    """Televize v HA, název zdroje a zařízení na účtu Oneplay, které je ta televize."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        cur = {**self.config_entry.data, **self.config_entry.options}
        errors: dict[str, str] = {}
        api = self.config_entry.runtime_data.api
        zarizeni: list[tuple[str, str]] = [("Jakékoli zařízení", "")]
        vychozi = cur.get(CONF_DEVICE_ID)
        posledni_titul = "—"
        profily: list[tuple[str, str]] = []
        try:
            devices = await api.devices_checked()
            profily = await api.profiles()
            tiles = await api.continue_watching()
            if tiles:
                tr = tiles[0].get("tracking") or {}
                nazev = (tr.get("parent") or {}).get("title") or tiles[0].get("title") or "?"
                posledni_titul = nazev + (f" — {tiles[0]['subTitle']}" if tiles[0].get("subTitle") else "")
        except (OneplayError, Exception):  # noqa: BLE001
            _LOGGER.exception("Oneplay: chyba při načtení zařízení")
            errors["base"] = "cannot_connect"
            devices = []
        pocet_streamujicich = sum(1 for d in devices if d.get("isStreaming"))
        for d in devices:
            if d.get("name") == DEVICE_NAME:
                continue
            popis = f"{d.get('name')} ({d.get('deviceType')}, naposledy {d.get('lastUsedAtFormatted')})"
            if d.get("isStreaming"):
                # Titul patří účtu/profilu, ne konkrétnímu zařízení — u jediného
                # streamujícího zařízení je přiřazení jednoznačné, u víc jich by
                # bylo zavádějící ho přičítat všem stejně.
                popis += (f" — právě streamuje ({posledni_titul})" if pocet_streamujicich == 1
                         else " — právě streamuje")
            zarizeni.append((popis, str(d.get("id"))))
            if vychozi is None and d.get("deviceType") == "smarttv":
                vychozi = str(d.get("id"))
        pole: dict[Any, Any] = {}
        if len(profily) > 1:
            pole[vol.Required(CONF_PROFILE_ID, default=cur.get(CONF_PROFILE_ID) or api.profile_id or profily[0][1])] = _select(profily)
        schema = vol.Schema({
            **pole,
            vol.Required(CONF_DEVICE_ID, default=vychozi or ""): _select(zarizeni),
            vol.Required(CONF_TV_ENTITY, default=cur.get(CONF_TV_ENTITY, DEFAULT_TV_ENTITY)):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="media_player")),
            vol.Required(CONF_TV_SOURCE, default=cur.get(CONF_TV_SOURCE, DEFAULT_TV_SOURCE)): str,
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors,
                                    description_placeholders={"posledni_titul": posledni_titul})
