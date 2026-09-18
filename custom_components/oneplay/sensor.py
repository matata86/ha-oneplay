"""sensor.oneplay_prehrava — naposledy sledovaný pořad v Oneplay a zda právě hraje."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import OneplayCoordinator
from .const import DOMAIN

ATRIBUTY = ("dil", "serie", "cislo_dilu", "typ", "kategorie", "content_id", "pozice_s",
            "procenta", "obrazek", "odkaz", "hraje", "tv_v_oneplay", "streamuje", "tv_streamuje", "aktualizovano")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([OneplayPrehrava(entry.runtime_data, entry)])


class OneplayPrehrava(CoordinatorEntity[OneplayCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "prehrava"
    _attr_icon = "mdi:television-play"

    def __init__(self, coordinator: OneplayCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_prehrava"
        self.entity_id = "sensor.oneplay_prehrava"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)},
                                  "name": "Oneplay", "manufacturer": "Oneplay",
                                  "entry_type": "service"}

    @property
    def native_value(self) -> str | None:
        return (self.coordinator.data or {}).get("nazev")

    @property
    def entity_picture(self) -> str | None:
        return (self.coordinator.data or {}).get("obrazek")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data or {}
        return {k: d.get(k) for k in ATRIBUTY}
