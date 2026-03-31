"""Persistent BLE connection manager for the K-WATCH.

The K-WATCH firmware does not set the "BR/EDR Not Supported" flag in its BLE
advertising data. BlueZ therefore treats it as dual-mode and tries classic
Bluetooth first, which always fails. We work around this by calling BlueZ's
Device1.Connect() directly via D-Bus, which (after the BR/EDR timeout) falls
back to LE. We then hand the connected device to bleak for GATT operations.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from bleak import BleakClient
from bleak.exc import BleakError
from dbus_fast import BusType
from dbus_fast.aio import MessageBus
from homeassistant.components.bluetooth import (
    async_ble_device_from_address,
    async_register_callback,
)
from homeassistant.components.bluetooth.match import BluetoothCallbackMatcher
from homeassistant.core import CALLBACK_TYPE, HomeAssistant

from .const import (
    INTER_PACKET_DELAY,
    RECONNECT_BASE_DELAY,
    RECONNECT_MAX_DELAY,
    RX_CHAR_UUID,
    TX_CHAR_UUID,
)
from .protocol import (
    encode_battery_request,
    encode_keepalive_response,
    encode_notification,
    encode_time_sync,
    parse_response,
)

_LOGGER = logging.getLogger(__name__)

BLUEZ_SERVICE = "org.bluez"
ADAPTER_PATH = "/org/bluez/hci0"

# BR/EDR page timeout is ~20s, then BlueZ tries LE which takes a few seconds.
_DBUS_CONNECT_TIMEOUT = 40.0


class KWatchBleClient:
    """Manages the persistent BLE connection to a K-WATCH device."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        on_data: Callable[[dict], None],
        on_connection_change: Callable[[bool], None],
    ) -> None:
        self._hass = hass
        self._address = address
        self._on_data = on_data
        self._on_connection_change = on_connection_change

        self._client: BleakClient | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._reconnect_delay = RECONNECT_BASE_DELAY
        self._shutting_down = False
        self._ble_callback_cancel: CALLBACK_TYPE | None = None
        self._connecting = False

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    def start_watching(self) -> None:
        """Register a BLE advertisement callback so we connect as soon as the watch is seen."""
        if self._ble_callback_cancel is not None:
            return
        self._ble_callback_cancel = async_register_callback(
            self._hass,
            self._on_ble_advertisement,
            BluetoothCallbackMatcher(address=self._address),
            mode="active",
        )
        _LOGGER.debug("Watching for K-WATCH %s advertisements", self._address)

    def _stop_watching(self) -> None:
        """Unregister the BLE advertisement callback."""
        if self._ble_callback_cancel:
            self._ble_callback_cancel()
            self._ble_callback_cancel = None

    def _on_ble_advertisement(self, service_info: Any, change: Any) -> None:
        """Called when HA's bluetooth scanner sees our device advertising."""
        if self.connected or self._shutting_down or self._connecting:
            return
        _LOGGER.debug("K-WATCH %s seen advertising, connecting...", self._address)
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            self._reconnect_task = None
        self._reconnect_delay = RECONNECT_BASE_DELAY
        self._hass.async_create_task(self.connect())

    async def _connect_via_dbus(self) -> bool:
        """Call BlueZ Device1.Connect() directly via D-Bus.

        This lets BlueZ handle the BR/EDR-to-LE fallback internally rather
        than going through bleak/bleak_retry_connector which may time out
        before the fallback completes.

        Returns True if the D-Bus connection succeeded.
        """
        device_path = (
            f"{ADAPTER_PATH}/dev_{self._address.replace(':', '_').upper()}"
        )
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        try:
            introspection = await bus.introspect(BLUEZ_SERVICE, device_path)
            device_obj = bus.get_proxy_object(
                BLUEZ_SERVICE, device_path, introspection
            )
            device1 = device_obj.get_interface("org.bluez.Device1")

            _LOGGER.debug(
                "Calling Device1.Connect() via D-Bus for %s (timeout=%ss)",
                self._address,
                _DBUS_CONNECT_TIMEOUT,
            )
            await asyncio.wait_for(
                device1.call_connect(), timeout=_DBUS_CONNECT_TIMEOUT
            )
            _LOGGER.debug("D-Bus Device1.Connect() succeeded for %s", self._address)
            return True
        except asyncio.TimeoutError:
            _LOGGER.debug("D-Bus Device1.Connect() timed out for %s", self._address)
            return False
        except Exception as err:
            _LOGGER.debug("D-Bus Device1.Connect() failed for %s: %s", self._address, err)
            return False
        finally:
            bus.disconnect()

    async def connect(self) -> None:
        """Establish BLE connection."""
        if self._connecting or self.connected:
            return
        self._connecting = True

        try:
            # First try connecting at the BlueZ level via D-Bus.
            # This handles the BR/EDR-to-LE fallback with a generous timeout.
            dbus_ok = await self._connect_via_dbus()
            if not dbus_ok:
                _LOGGER.warning(
                    "K-WATCH %s: BlueZ D-Bus connect failed, will retry",
                    self._address,
                )
                self._schedule_reconnect()
                return

            # BlueZ has connected. Now wrap with BleakClient for GATT.
            # BleakClient.connect() on an already-connected device just
            # discovers services.
            ble_device = async_ble_device_from_address(
                self._hass, self._address, connectable=True
            )
            if not ble_device:
                _LOGGER.warning(
                    "K-WATCH %s: connected at BlueZ level but not in HA cache",
                    self._address,
                )
                self._schedule_reconnect()
                return

            self._client = BleakClient(
                ble_device,
                disconnected_callback=self._on_disconnect,
            )
            await self._client.connect()

            await self._client.start_notify(RX_CHAR_UUID, self._on_notification)

            self._reconnect_delay = RECONNECT_BASE_DELAY
            self._on_connection_change(True)
            _LOGGER.info("Connected to K-WATCH %s", self._address)

            await self._write(encode_time_sync())
            await asyncio.sleep(INTER_PACKET_DELAY)
            await self._write(encode_battery_request())

        except (BleakError, TimeoutError, OSError) as err:
            _LOGGER.warning("Failed to connect to K-WATCH %s: %s", self._address, err)
            self._schedule_reconnect()
        finally:
            self._connecting = False

    async def disconnect(self) -> None:
        """Disconnect and stop reconnection attempts."""
        self._shutting_down = True
        self._stop_watching()
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            self._reconnect_task = None
        if self._client and self._client.is_connected:
            try:
                await self._client.stop_notify(RX_CHAR_UUID)
            except BleakError:
                pass
            try:
                await self._client.disconnect()
            except BleakError:
                pass
        self._client = None

    async def send_message(self, title: str, body: str, type_id: int = 1) -> None:
        """Send a notification message to the watch as a multi-packet sequence."""
        if not self.connected:
            raise ConnectionError("Not connected to K-WATCH")

        packets = encode_notification(title, body, type_id)
        for packet in packets:
            await self._write(packet)
            await asyncio.sleep(INTER_PACKET_DELAY)

    async def _write(self, data: bytes) -> None:
        """Write a packet to the TX characteristic.

        Mirrors the Android SDK strategy: try write-with-response first,
        fall back to write-without-response, up to 3 attempts.
        """
        if not self.connected:
            raise ConnectionError("Not connected to K-WATCH")
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                response = attempt == 0
                await self._client.write_gatt_char(TX_CHAR_UUID, data, response=response)
                return
            except BleakError as err:
                last_err = err
                _LOGGER.debug("Write attempt %d failed: %s", attempt + 1, err)
                await asyncio.sleep(0.3)
        raise last_err

    def _on_notification(self, _sender: Any, data: bytearray) -> None:
        """Handle incoming BLE notification from the watch."""
        parsed = parse_response(data)

        if parsed["type"] == "keepalive":
            self._hass.async_create_task(self._respond_keepalive())
            return

        self._on_data(parsed)

    async def _respond_keepalive(self) -> None:
        """Send keepalive response back to the watch."""
        try:
            await self._write(encode_keepalive_response())
        except (BleakError, ConnectionError) as err:
            _LOGGER.debug("Failed to send keepalive response: %s", err)

    def _on_disconnect(self, _client: BleakClient) -> None:
        """Handle unexpected disconnection."""
        _LOGGER.info("K-WATCH %s disconnected", self._address)
        self._client = None
        self._on_connection_change(False)
        if not self._shutting_down:
            self.start_watching()
            self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        """Schedule a reconnection attempt with exponential backoff."""
        if self._shutting_down:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return

        delay = self._reconnect_delay
        self._reconnect_delay = min(
            self._reconnect_delay * 2, RECONNECT_MAX_DELAY
        )
        _LOGGER.debug("Scheduling reconnect in %ds", delay)
        self._reconnect_task = self._hass.async_create_task(self._reconnect(delay))

    async def _reconnect(self, delay: float) -> None:
        """Wait and then attempt to reconnect."""
        try:
            await asyncio.sleep(delay)
            if not self._shutting_down:
                await self.connect()
        finally:
            self._reconnect_task = None
