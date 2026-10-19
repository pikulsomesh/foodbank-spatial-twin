// The viewer's offline simulator must keep the same contract and layout as the Lambda.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { simulate, FORKLIFT_PATH } from "../public/js/telemetry.js";

const lambda = readFileSync(new URL("../src/telemetry/app.py", import.meta.url), "utf8");

test("payload has the telemetry contract fields", () => {
  const d = simulate(1_800_000_000_000);
  assert.equal(d.simulated, true);
  for (const z of d.zones) {
    for (const k of ["id", "label", "temperature_c", "setpoint_c", "humidity_pct", "door_open", "status", "position"]) assert.ok(k in z, k);
    assert.ok(["ok", "warn", "alarm"].includes(z.status));
  }
  for (const k of ["id", "position", "heading_deg", "speed_mps", "battery_pct"]) assert.ok(k in d.forklifts[0], k);
});

test("zones and forklift path match src/telemetry/app.py", () => {
  const d = simulate(1_800_000_000_000);
  for (const z of d.zones) {
    const m = lambda.match(new RegExp(`"id": "${z.id}".*?"setpoint_c": ([-\\d.]+).*?"position": \\[([^\\]]+)\\]`));
    assert.ok(m, `zone ${z.id} missing from app.py`);
    assert.equal(z.setpoint_c, Number(m[1]));
    assert.deepEqual(z.position, m[2].split(",").map(Number));
  }
  const path = lambda.match(/FORKLIFT_PATH = (\[\[.*\]\])/)[1];
  assert.deepEqual(FORKLIFT_PATH, JSON.parse(path));
});

test("freezer stays near setpoint and is deterministic", () => {
  for (let t = 0; t < 7200; t += 37) {
    const ms = (1.8e9 + t) * 1000;
    assert.deepEqual(simulate(ms), simulate(ms));
    const f = simulate(ms).zones.find((z) => z.id === "freezer-1");
    assert.ok(f.temperature_c > -22 && f.temperature_c < -14, String(f.temperature_c));
  }
});
