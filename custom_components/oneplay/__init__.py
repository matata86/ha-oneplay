"""Oneplay — co se právě přehrává v aplikaci Oneplay (např. na Samsung TV).

Zdroj: řada „Pokračovat ve sledování“ na účtu. Na API se ptá jen tehdy, když
má TV nastavený zdroj Oneplay.

Tile[0] (nejvýš v řadě) NENÍ spolehlivě „to, co se hraje“ — ověřeno 2026-09-18
večer: probíhající živý zápas (epgitem) vyskočil na první místo, přestože jeho
pozice zůstala celé hodiny stejná (111 s), zatímco skutečně sledovaný pořad
o pár míst níž měl pozici rostoucí. Oneplay řadu zjevně řadí i podle toho, co
právě běží živě, ne jen podle posledního sledování. Proto se prochází víc
dlaždic a vybírá se ta, jejíž pozice se od minulého dotazu doopravdy posunula.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import OneplayApi, OneplayError
from .const import (CONF_ACCOUNT_ID, CONF_DEVICE_ID, CONF_PROFILE_ID, CONF_TV_ENTITY, CONF_TV_SOURCE,
                    DEFAULT_TV_ENTITY, DEFAULT_TV_SOURCE, DOMAIN, SCAN_INTERVAL_S)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]


def parse_tile(tile: dict) -> dict[str, Any]:
    tr = tile.get("tracking") or {}
    parent = tr.get("parent") or {}
    prog = tile.get("progress") or {}
    route = (tile.get("action") or {}).get("route") or {}
    img = ((tile.get("image") or {}).get("image") or "").replace("{WIDTH}x{HEIGHT}", "640x360")
    return {
        "nazev": parent.get("title") or tile.get("title") or tr.get("title"),
        "dil": tile.get("subTitle") if tr.get("type") == "episode" else None,
        "popis": tile.get("subTitle"),
        "serie": tr.get("season"),
        "cislo_dilu": tr.get("episodeNumber"),
        "typ": tr.get("type"),
        "kategorie": tr.get("category"),
        "content_id": tr.get("id"),
        "pozice_s": prog.get("position"),
        "procenta": prog.get("percent"),
        "obrazek": img or None,
        "odkaz": route.get("url"),
    }


class OneplayCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(seconds=SCAN_INTERVAL_S))
        d = entry.data
        self.api = OneplayApi(async_get_clientsession(hass), d[CONF_EMAIL], d[CONF_PASSWORD],
                              d.get(CONF_ACCOUNT_ID), d.get(CONF_PROFILE_ID))
        o = {**d, **entry.options}
        self.tv_entity = o.get(CONF_TV_ENTITY, DEFAULT_TV_ENTITY)
        self.tv_source = o.get(CONF_TV_SOURCE, DEFAULT_TV_SOURCE)
        self.device_id = str(o.get(CONF_DEVICE_ID) or "")
        self._pozice: dict[str, tuple] = {}   # content_id -> (pozice, procenta) z minulého dotazu
        self._vybrany: str | None = None      # content_id dlaždice, kterou teď považujeme za „hraje se“
        self._stejne = 0                      # kolik dotazů po sobě se u vybrané dlaždice nic nezměnilo

    def tv_v_oneplay(self) -> bool:
        st = self.hass.states.get(self.tv_entity)
        return bool(st and st.state not in ("off", "unavailable", "unknown")
                    and st.attributes.get("source") == self.tv_source)

    async def _async_update_data(self) -> dict[str, Any]:
        aktivni = self.tv_v_oneplay()
        if not aktivni and self.data:
            # TV není v Oneplay — API se neptáme, jen shodíme „hraje“
            self._pozice = {}
            self._vybrany = None
            return {**self.data, "hraje": False, "tv_v_oneplay": False}
        try:
            tiles = await self.api.continue_watching()
            devices = await self.api.devices_checked()
        except OneplayError as err:
            raise UpdateFailed(str(err)) from err
        except Exception as err:  # noqa: BLE001 — síť/timeout
            raise UpdateFailed(f"Oneplay nedostupný: {err}") from err

        streamuje = [d.get("name") for d in devices if d.get("isStreaming")]
        # Vybrané zařízení (TV) na účtu streamuje? Bez výběru stačí jakékoli.
        tv_streamuje = any(d.get("isStreaming") and (not self.device_id or str(d.get("id")) == self.device_id)
                           for d in devices)

        # Mezi dlaždicemi vybrat tu, jejíž pozice se od minula doopravdy posunula
        # (viz poznámka nahoře — první místo si sem tam vezme živě běžící pořad,
        # na který se nikdo nedívá). Pozice se ukládá po desítkách sekund, proto
        # jeden stejný vzorek po sobě ještě neznamená pauzu daného pořadu.
        pozice_nyni: dict[str, tuple] = {}
        posunuta: dict | None = None
        for tile in tiles:
            cid = (tile.get("tracking") or {}).get("id")
            if not cid:
                continue
            prog = tile.get("progress") or {}
            klic = (prog.get("position"), prog.get("percent"))
            pozice_nyni.setdefault(cid, klic)
            if posunuta is None and cid in self._pozice and klic != self._pozice[cid]:
                posunuta = tile

        if aktivni and posunuta is not None:
            data = parse_tile(posunuta)
            self._vybrany, hraje, self._stejne = data.get("content_id"), True, 0
        elif aktivni and self._vybrany and any(
                (t.get("tracking") or {}).get("id") == self._vybrany for t in tiles):
            # Nic se neposunulo, ale dlaždice, u které jsme to naposled viděli,
            # v řadě pořád je — bereme to jako pauzu na tom samém pořadu.
            data = parse_tile(next(t for t in tiles if (t.get("tracking") or {}).get("id") == self._vybrany))
            self._stejne += 1
            hraje = False
        elif tiles:
            data = parse_tile(tiles[0])
            self._vybrany, hraje, self._stejne = None, False, 0
        else:
            data, hraje = {}, False

        # „Pokračovat ve sledování“ patří profilu, ne zařízení — pozici může posouvat
        # i tablet na stejném profilu. S vybraným zařízením hraje jen při jeho streamu.
        if self.device_id and not tv_streamuje:
            hraje = False
        self._pozice = pozice_nyni if aktivni else {}
        data.update({
            "hraje": hraje,
            "tv_v_oneplay": aktivni,
            "streamuje": streamuje,
            "tv_streamuje": tv_streamuje,
            "aktualizovano": dt_util.utcnow().isoformat(),
        })
        return data


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = OneplayCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    @callback
    def _tv_zmena(event: Event) -> None:
        old, new = event.data.get("old_state"), event.data.get("new_state")
        src = lambda s: s and s.state not in ("off", "unavailable") and s.attributes.get("source")
        if src(old) != src(new):
            hass.async_create_task(coordinator.async_request_refresh())

    entry.async_on_unload(async_track_state_change_event(hass, [coordinator.tv_entity], _tv_zmena))
    entry.async_on_unload(entry.add_update_listener(_options_zmena))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _options_zmena(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
