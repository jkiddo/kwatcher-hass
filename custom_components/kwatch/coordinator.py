"""Data coordinator for the K-Watch Messenger integration."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .ble_client import KWatchBleClient
from .const import (
    CONF_DEVICE_ADDRESS,
    DEFAULT_MESSAGE_TIMEOUT,
    DOMAIN,
    MAX_HISTORY_ENTRIES,
    RESPONSE_IDLE,
    RESPONSE_NO,
    RESPONSE_OK,
    RESPONSE_PENDING,
    RESPONSE_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


class KWatchCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate K-WATCH BLE data and messaging state."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=entry.title,
            update_interval=None,  # Push-based, no polling
        )
        self.entry = entry
        self._address = entry.data[CONF_DEVICE_ADDRESS]
        self._timeout_handle: Any | None = None

        self.data: dict[str, Any] = {
            "battery_level": None,
            "battery_charging": None,
            "connected": False,
            "last_message": None,
            "last_message_time": None,
            "last_response": RESPONSE_IDLE,
            "last_response_time": None,
            "message_history": [],
        }

        self.ble_client = KWatchBleClient(
            hass,
            self._address,
            on_data=self._handle_data,
            on_connection_change=self._handle_connection_change,
        )

    async def async_setup(self) -> None:
        """Start the BLE connection."""
        await self.ble_client.connect()

    @callback
    def _handle_data(self, parsed: dict) -> None:
        """Handle parsed BLE data from the watch."""
        data = {**self.data}

        if parsed["type"] == "battery":
            data["battery_level"] = parsed["level"]
            data["battery_charging"] = parsed["charging"]

        elif parsed["type"] == "event":
            action = parsed.get("action")
            if action in ("ok", "no"):
                self._resolve_pending_message(
                    data, RESPONSE_OK if action == "ok" else RESPONSE_NO
                )

        self.async_set_updated_data(data)

    @callback
    def _handle_connection_change(self, connected: bool) -> None:
        """Handle BLE connection state changes."""
        data = {**self.data}
        data["connected"] = connected
        self.async_set_updated_data(data)

    async def send_message(self, title: str, body: str) -> None:
        """Send a message to the watch and track it."""
        await self.ble_client.send_message(title, body)

        now = datetime.now(timezone.utc)
        data = {**self.data}

        # If there was a pending message, mark it as timed out
        if data["last_response"] == RESPONSE_PENDING:
            self._resolve_pending_message(data, RESPONSE_TIMEOUT)

        data["last_message"] = body
        data["last_message_time"] = now.isoformat()
        data["last_response"] = RESPONSE_PENDING
        data["last_response_time"] = None

        # Add to history
        history = list(data["message_history"])
        history.insert(0, {
            "message": body,
            "title": title,
            "sent_at": now.isoformat(),
            "response": None,
            "responded_at": None,
        })
        data["message_history"] = history[:MAX_HISTORY_ENTRIES]

        self.async_set_updated_data(data)

        # Schedule timeout
        self._cancel_timeout()
        self._timeout_handle = self.hass.loop.call_later(
            DEFAULT_MESSAGE_TIMEOUT, self._on_message_timeout
        )

    def _resolve_pending_message(self, data: dict, response: str) -> None:
        """Resolve the current pending message with a response."""
        self._cancel_timeout()
        now = datetime.now(timezone.utc)

        data["last_response"] = response
        data["last_response_time"] = now.isoformat()

        # Update the most recent history entry
        history = list(data["message_history"])
        if history and history[0]["response"] is None:
            history[0] = {**history[0], "response": response, "responded_at": now.isoformat()}
            data["message_history"] = history

        # Fire HA event for automations
        self.hass.bus.async_fire(
            f"{DOMAIN}_response",
            {
                "device_name": self.entry.title,
                "message": data.get("last_message", ""),
                "response": response,
                "timestamp": now.isoformat(),
            },
        )

    @callback
    def _on_message_timeout(self) -> None:
        """Handle message response timeout."""
        if self.data.get("last_response") != RESPONSE_PENDING:
            return
        data = {**self.data}
        self._resolve_pending_message(data, RESPONSE_TIMEOUT)
        self.async_set_updated_data(data)

    def _cancel_timeout(self) -> None:
        """Cancel any pending message timeout."""
        if self._timeout_handle is not None:
            self._timeout_handle.cancel()
            self._timeout_handle = None
