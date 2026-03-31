/**
 * K-WATCH BLE-to-MQTT Bridge
 *
 * Connects to a K-WATCH via BLE and bridges messages to/from Home Assistant
 * via MQTT with auto-discovery.
 */

const config = require('./config');
const BleConnection = require('./ble/connection');
const { encodeTimeSync, encodeBatteryRequest, encodeNotification, encodeVibrate } = require('./ble/protocol');
const { fetchAndEncodeWeather } = require('./weather');
const MqttBridge = require('./mqtt/client');
const { publishDiscovery } = require('./mqtt/discovery');
const HistoryManager = require('./mqtt/history');

const ble = new BleConnection(config);
const mqtt = new MqttBridge(config);
const history = new HistoryManager(config, mqtt);

let batteryInterval = null;
let lastBatteryPayload = null;

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function writePackets(packets, delay) {
  for (const pkt of packets) {
    await ble.write(pkt);
    await sleep(delay);
  }
}

// ── BLE Events ──────────────────────────────────────────────────────────

ble.on('connected', async () => {
  console.log('[BRIDGE] Watch connected');
  mqtt.publishRetained('device/connection', 'online');

  try {
    await ble.write(encodeTimeSync());
    await sleep(config.interPacketDelay);
    await ble.write(encodeBatteryRequest());
  } catch (err) {
    console.error(`[BRIDGE] Handshake failed: ${err.message}`);
  }

  // Clear any stacked interval from rapid reconnects
  clearInterval(batteryInterval);
  batteryInterval = setInterval(async () => {
    try {
      await ble.write(encodeBatteryRequest());
    } catch (_) {}
  }, config.batteryPollInterval * 1000);
});

ble.on('disconnected', () => {
  console.log('[BRIDGE] Watch disconnected');
  mqtt.publishRetained('device/connection', 'offline');
  clearInterval(batteryInterval);
  batteryInterval = null;
  lastBatteryPayload = null;
});

ble.on('data', (parsed) => {
  if (parsed.type === 'battery') {
    const payload = JSON.stringify({ level: parsed.level, charging: parsed.charging });
    if (payload !== lastBatteryPayload) {
      lastBatteryPayload = payload;
      mqtt.publishRetained('device/battery', payload);
    }
  } else if (parsed.type === 'event') {
    if (parsed.action === 'ok' || parsed.action === 'no') {
      const response = parsed.action === 'ok' ? 'OK - got it' : 'No';
      history.resolveMessage(response);
      mqtt.publish('device/event', JSON.stringify({
        action: parsed.action,
        timestamp: new Date().toISOString(),
      }));
    }
  }
});

// ── MQTT Events ─────────────────────────────────────────────────────────

mqtt.on('command', async (topic, payload) => {
  try {
    if (topic.endsWith('send_message')) {
      const { title = 'HA', message } = JSON.parse(payload.toString());
      if (!message) return;
      if (!ble.connected) { console.log('[BRIDGE] Cannot send: not connected'); return; }

      console.log(`[BRIDGE] Sending message: "${message}" (title: "${title}")`);
      await writePackets(encodeNotification(title, message), config.interPacketDelay);
      history.addMessage(title, message);

    } else if (topic.endsWith('clear_history')) {
      console.log('[BRIDGE] Clearing message history');
      history.clear();

    } else if (topic.endsWith('vibrate')) {
      if (!ble.connected) { console.log('[BRIDGE] Cannot vibrate: not connected'); return; }
      console.log('[BRIDGE] Vibrating watch');
      await writePackets(encodeVibrate(), 300);

    } else if (topic.endsWith('sync_time')) {
      if (!ble.connected) { console.log('[BRIDGE] Cannot sync time: not connected'); return; }
      console.log('[BRIDGE] Syncing time to watch');
      await ble.write(encodeTimeSync());

    } else if (topic.endsWith('sync_weather')) {
      if (!ble.connected) { console.log('[BRIDGE] Cannot sync weather: not connected'); return; }
      console.log('[BRIDGE] Fetching weather from OpenWeatherMap...');
      const packets = await fetchAndEncodeWeather(config);
      console.log(`[BRIDGE] Syncing ${packets.length} days of weather to watch`);
      await writePackets(packets, config.interPacketDelay);
    }
  } catch (err) {
    console.error(`[BRIDGE] Command "${topic}" failed: ${err.message}`);
  }
});

// ── Startup ─────────────────────────────────────────────────────────────

async function main() {
  console.log('[BRIDGE] Starting K-WATCH BLE-to-MQTT bridge');

  await mqtt.connect();
  publishDiscovery(mqtt, config);
  history.load();

  ble.start().catch(err => {
    console.error(`[BRIDGE] BLE start failed: ${err.message}`);
  });
}

// ── Shutdown ────────────────────────────────────────────────────────────

async function shutdown() {
  console.log('[BRIDGE] Shutting down...');
  clearInterval(batteryInterval);
  await ble.stop();
  mqtt.publishRetained('device/connection', 'offline');
  await sleep(500);
  mqtt.disconnect();
  process.exit(0);
}

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

main().catch(err => {
  console.error(`[BRIDGE] Fatal: ${err.message}`);
  process.exit(1);
});
