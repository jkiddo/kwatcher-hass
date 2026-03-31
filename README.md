# K-Watch Messenger

A Home Assistant custom integration that turns a [K-WATCH](https://github.com/jkiddo/watch) BLE fitness tracker into a two-way messaging device.

Send text messages from Home Assistant's web UI to the watch. The watch wearer responds using existing button actions:

- **"Take Photo" button** = **OK - got it**
- **"Find Device" button** = **No**

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations** > **Custom repositories**
3. Add `https://github.com/jkiddo/kwatcher-haas` as an **Integration**
4. Install **K-Watch Messenger**
5. Restart Home Assistant

### Manual

Copy the `custom_components/kwatch` directory to your Home Assistant `config/custom_components/` directory and restart.

## Setup

After installation:

1. Go to **Settings > Devices & Services > Add Integration**
2. Search for **K-Watch Messenger**
3. If the watch is nearby and advertising, select it from the list. Otherwise, enter the BLE MAC address manually.

The integration maintains a persistent BLE connection with automatic reconnection.

## Usage

### Lovelace Card

Add the card resource under **Settings > Dashboards > Resources**:

- **URL:** `/kwatch/kwatch-message-card.js`
- **Type:** JavaScript Module

Then add the card to a dashboard (Edit > Add Card > Manual):

```yaml
type: custom:kwatch-message-card
response_entity: sensor.k_watch_last_response
battery_entity: sensor.k_watch_battery
connection_entity: sensor.k_watch_connection
```

The card provides a message input, send button, connection status, battery indicator, and a scrollable message history with response badges.

### Service

Call `kwatch.send_message` from automations, scripts, or Developer Tools:

```yaml
service: kwatch.send_message
data:
  message: "Dinner is ready"
  title: "Home"  # optional, default: "HA"
```

### Automation Events

The integration fires a `kwatch_response` event when the watch wearer responds:

```yaml
automation:
  trigger:
    platform: event
    event_type: kwatch_response
    event_data:
      response: "No"
  action:
    - service: notify.mobile_app
      data:
        message: "Watch wearer declined"
```

Event data:

| Field | Example |
|-------|---------|
| `device_name` | `K-WATCH` |
| `message` | `Dinner is ready` |
| `response` | `OK - got it` or `No` |
| `timestamp` | `2026-03-31T14:30:00+00:00` |

## Entities

| Entity | Type | Description |
|--------|------|-------------|
| `sensor.k_watch_last_response` | Sensor | Last response from watch wearer (OK - got it / No / Pending / No response) |
| `sensor.k_watch_battery` | Sensor | Battery level (%) |
| `sensor.k_watch_connection` | Sensor | BLE connection status (Connected / Disconnected) |

The response sensor includes a `message_history` attribute with the last 50 messages and their responses.

## How It Works

The K-WATCH uses a proprietary BLE protocol over a custom GATT service (`56ff`). Messages are sent as multi-packet notification sequences (command `0x46`). The watch displays incoming messages on screen. When the wearer presses a button, the watch sends an event (`0x06`) back over BLE:

- Event code `0x02` (Take Photo) is interpreted as **OK - got it**
- Event code `0x01` (Find Device) is interpreted as **No**

If no response is received within 120 seconds, the message is marked **No response**.

The integration handles BLE keepalive pings (`0x3A`) automatically to maintain the connection.

## Requirements

- Home Assistant 2023.1.0 or newer
- A Bluetooth adapter accessible to Home Assistant
- A K-WATCH BLE fitness tracker

## License

MIT
