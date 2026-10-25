// Telemetry feed for the viewer: the AWS API when it answers, a local simulator when it doesn't.
// The simulator mirrors src/telemetry/app.py (same zones, setpoints, bands, forklift loop and
// JSON contract), so the page looks the same whether or not the backend is deployed.

const ZONES = [
  { id: "freezer-1", label: "Freezer", setpoint_c: -18.0, band_c: 3.0, position: [4.0, 1.5, 3.0] },
  { id: "cooler-1", label: "Cooler", setpoint_c: 3.0, band_c: 2.5, position: [12.0, 1.5, 3.0] },
  { id: "dry-1", label: "Dry Storage", setpoint_c: 21.0, band_c: 6.0, position: [20.0, 1.5, 8.0] },
  { id: "kitchen-1", label: "Kitchen Walk-in", setpoint_c: 3.0, band_c: 2.5, position: [36.5, 1.5, 9.0] },
];
export const FORKLIFT_PATH = [[1, 0, 12], [4, 0, 6], [12, 0, 6], [20, 0, 10], [12, 0, 12]];
const LOOP_SECONDS = 120;

// Repeatable value in [-1, 1] for (key, bucket). Not bit-identical to Python's random.Random,
// but the same shape: fixed per sensor per time bucket.
function noise(key, bucket) {
  let h = 2166136261;
  for (const ch of `${key}:${bucket}`) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  h = Math.imul(h ^ (h >>> 15), 2246822507);
  h = Math.imul(h ^ (h >>> 13), 3266489909);
  return (((h ^ (h >>> 16)) >>> 0) / 4294967295) * 2 - 1;
}
const round = (v, d = 1) => Math.round(v * 10 ** d) / 10 ** d;

function zone(z, now) {
  const drift = Math.sin((2 * Math.PI * now) / 900 + z.id.length) * 0.4 * z.band_c;
  const temp = z.setpoint_c + drift + noise(z.id, Math.floor(now / 5)) * 0.15 * z.band_c;
  const dev = Math.abs(temp - z.setpoint_c);
  const status = dev > z.band_c ? "alarm" : dev > 0.7 * z.band_c ? "warn" : "ok";
  const humidity = 60 + 10 * Math.sin((2 * Math.PI * now) / 1800) + noise(z.id + "h", Math.floor(now / 5)) * 2;
  const doorOpen = (noise(`${z.id}:door`, Math.floor(now / 30)) + 1) / 2 < 0.1;
  return {
    id: z.id, label: z.label, temperature_c: round(temp), setpoint_c: z.setpoint_c,
    humidity_pct: round(humidity), door_open: doorOpen, status, position: z.position,
  };
}

export function forklift(now) {
  const n = FORKLIFT_PATH.length;
  const t = ((now % LOOP_SECONDS) / LOOP_SECONDS) * n;
  const i = Math.floor(t) % n, f = t - Math.floor(t);
  const a = FORKLIFT_PATH[i], b = FORKLIFT_PATH[(i + 1) % n];
  const pos = a.map((v, k) => round(v + (b[k] - v) * f, 2));
  const heading = ((Math.atan2(b[0] - a[0], b[2] - a[2]) * 180) / Math.PI + 360) % 360;
  const len = Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
  return {
    id: "forklift-1", label: "Forklift", position: pos, heading_deg: round(heading),
    speed_mps: round((len * n) / LOOP_SECONDS, 2), battery_pct: round(100 - ((now % 14400) / 14400) * 70),
  };
}

export function simulate(nowMs = Date.now()) {
  const now = nowMs / 1000;
  const zones = ZONES.map((z) => zone(z, now));
  return {
    site: "Representative food bank (inspired by Central Texas Food Bank)", simulated: true,
    timestamp: new Date(Math.floor(now) * 1000).toISOString().replace(".000", ""),
    units: { temperature: "C", position: "m", speed: "m/s" },
    zones, forklifts: [forklift(now)],
    alerts: zones.filter((z) => z.status === "alarm").map((z) => `${z.label} ${z.temperature_c}C out of range`),
  };
}

const valid = (d) => d && Array.isArray(d.zones) && Array.isArray(d.forklifts);

// Polls `${apiBase}/telemetry`. Calls onData(payload, source) with source "live" or "simulated".
// After a failure it serves simulated data and re-tries the API every `retryMs`.
export function startFeed({ apiBase, onData, pollMs = 2000, retryMs = 30000, forceSim = false }) {
  const url = apiBase ? apiBase.replace(/\/+$/, "") + "/telemetry" : null;
  let live = !!url && !forceSim, nextTry = 0, stopped = false;

  async function tick() {
    if (stopped) return;
    if (live || (url && !forceSim && Date.now() >= nextTry)) {
      try {
        const ctl = new AbortController();
        const timer = setTimeout(() => ctl.abort(), 3000);
        const r = await fetch(url, { signal: ctl.signal, cache: "no-store" });
        clearTimeout(timer);
        const d = r.ok ? await r.json() : null;
        if (!valid(d)) throw new Error(`bad response ${r.status}`);
        live = true;
        onData(d, "live");
      } catch (e) {
        if (live) console.info("Telemetry API unreachable, using the simulated feed:", e.message || e);
        live = false;
        nextTry = Date.now() + retryMs;
        onData(simulate(), "simulated");
      }
    } else {
      onData(simulate(), "simulated");
    }
    setTimeout(tick, pollMs);
  }
  tick();
  return () => { stopped = true; };
}
