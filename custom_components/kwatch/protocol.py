"""K-WATCH BLE protocol encoding/decoding.

Pure functions with no Home Assistant or bleak dependency.
Packet format: 20 bytes, zero-padded, little-endian multi-byte values.
"""

from __future__ import annotations

import struct
import time

from .const import (
    CMD_KEEPALIVE,
    CMD_NOTIFICATION,
    CMD_TIME_SYNC,
    CMD_BATTERY,
    EVENT_FIND_PHONE,
    EVENT_TAKE_PHOTO,
    PACKET_PAYLOAD_SIZE,
    PACKET_SIZE,
    RESP_BATTERY,
    RESP_EVENT,
    RESP_KEEPALIVE,
)


def encode_notification(
    title: str, body: str, type_id: int = 1
) -> list[bytes]:
    """Encode a notification as a multi-packet sequence.

    Returns a list of 20-byte packets ready to write to the TX characteristic.
    Protocol: 0x46, totalPackets, seqId, then payload.
    """
    title_bytes = _utf8_truncate(title or "", PACKET_PAYLOAD_SIZE)
    body_bytes = (body or "").encode("utf-8")

    # Split body into 17-byte chunks (at least 1 chunk even if empty)
    body_chunks: list[bytes] = []
    if len(body_bytes) == 0:
        body_chunks.append(b"")
    else:
        for i in range(0, len(body_bytes), PACKET_PAYLOAD_SIZE):
            body_chunks.append(body_bytes[i : i + PACKET_PAYLOAD_SIZE])

    total_packets = 2 + len(body_chunks)  # header + title + body chunks
    packets: list[bytes] = []

    # Packet 1: Header
    pkt = bytearray(PACKET_SIZE)
    pkt[0] = CMD_NOTIFICATION
    pkt[1] = total_packets
    pkt[2] = 1  # sequence ID
    pkt[3] = 0x00
    pkt[4] = type_id & 0xFF
    packets.append(bytes(pkt))

    # Packet 2: Title
    pkt = bytearray(PACKET_SIZE)
    pkt[0] = CMD_NOTIFICATION
    pkt[1] = total_packets
    pkt[2] = 2
    pkt[3 : 3 + len(title_bytes)] = title_bytes
    packets.append(bytes(pkt))

    # Packets 3+: Body chunks
    for idx, chunk in enumerate(body_chunks):
        pkt = bytearray(PACKET_SIZE)
        pkt[0] = CMD_NOTIFICATION
        pkt[1] = total_packets
        pkt[2] = 3 + idx
        pkt[3 : 3 + len(chunk)] = chunk
        packets.append(bytes(pkt))

    return packets


def encode_time_sync(tz_offset_hours: int | None = None) -> bytes:
    """Encode a time sync command (0x01).

    Uses the current Unix timestamp. tz_offset_hours defaults to local timezone.
    """
    now = int(time.time())
    if tz_offset_hours is None:
        tz_offset_hours = -time.timezone // 3600
    pkt = bytearray(PACKET_SIZE)
    pkt[0] = CMD_TIME_SYNC
    pkt[1:5] = struct.pack("<I", now)
    pkt[5] = tz_offset_hours & 0xFF
    return bytes(pkt)


def encode_keepalive_response() -> bytes:
    """Encode a keepalive response (0x3A)."""
    pkt = bytearray(PACKET_SIZE)
    pkt[0] = CMD_KEEPALIVE
    return bytes(pkt)


def encode_battery_request() -> bytes:
    """Encode a battery level request (0x0B)."""
    pkt = bytearray(PACKET_SIZE)
    pkt[0] = CMD_BATTERY
    return bytes(pkt)


def parse_response(data: bytes | bytearray) -> dict:
    """Parse a 20-byte response from the device.

    Returns a dict with at minimum a "type" key.
    """
    if not data or len(data) < 2:
        return {"type": "unknown", "raw": bytes(data) if data else b""}

    resp_id = data[0]

    if resp_id == RESP_EVENT:
        event_code = data[1]
        if event_code == EVENT_TAKE_PHOTO:
            return {"type": "event", "event_code": event_code, "action": "ok"}
        if event_code == EVENT_FIND_PHONE:
            return {"type": "event", "event_code": event_code, "action": "no"}
        return {"type": "event", "event_code": event_code, "action": "other"}

    if resp_id == RESP_BATTERY:
        return {
            "type": "battery",
            "level": data[1],
            "charging": bool(data[2]),
        }

    if resp_id == RESP_KEEPALIVE:
        return {"type": "keepalive"}

    return {"type": "unknown", "raw": bytes(data)}


def _utf8_truncate(text: str, max_bytes: int) -> bytes:
    """Encode a string to UTF-8 and truncate at a safe byte boundary."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return encoded
    # Truncate and re-decode to avoid splitting a multi-byte character
    truncated = encoded[:max_bytes]
    return truncated.decode("utf-8", errors="ignore").encode("utf-8")
