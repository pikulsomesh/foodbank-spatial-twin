// The page's local operations model must give the same payload as the Lambda's /operations.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { simulateOps } from "../public/js/operations.js";
import { simulate } from "../public/js/telemetry.js";

const data = JSON.parse(readFileSync(new URL("../public/ops/operations.json", import.meta.url), "utf8"));
const times = [0, 3, 5.75, 6.5, 8, 9.25, 11, 13.5, 15, 17, 22].map((h) => 1_800_000_000 + h * 3600)
  .concat([2, 3, 5].map((d) => 1_800_000_000 + d * 86400 + 10 * 3600));

// Python's sensor model differs in its noise source, so both sides get the JS cold zones.
const cold = Object.fromEntries(times.map((t) => [t, simulate(t * 1000).zones]));
const py = JSON.parse(execFileSync("python3", ["-c", `
import json, sys
sys.path.insert(0, "src/telemetry")
import operations
cold = json.loads(sys.stdin.read())
print(json.dumps({t: operations.build_operations(float(t), z) for t, z in cold.items()}))
`], { input: JSON.stringify(cold), cwd: new URL("..", import.meta.url).pathname }).toString());

function close(a, b, path) {
  if (typeof a === "number" && typeof b === "number") {
    assert.ok(Math.abs(a - b) <= 1 + 1e-9 * Math.abs(b), `${path}: js ${a} vs py ${b}`);
  } else if (a && typeof a === "object") {
    assert.deepEqual(Object.keys(a).sort(), Object.keys(b).sort(), path);
    for (const k of Object.keys(a)) close(a[k], b[k], `${path}.${k}`);
  } else assert.equal(a, b, path);
}

test("JS operations model matches src/telemetry/operations.py", () => {
  for (const t of times) close(simulateOps(data, t * 1000, cold[t]), py[t], `t=${t}`);
});

test("operations payload has the contract fields", () => {
  const d = simulateOps(data, 1_800_000_000_000 + 4 * 3600e3, simulate(1_800_000_000_000).zones);
  for (const k of ["areas", "inventory", "flows", "kitchen", "trucks", "kpis"]) assert.ok(k in d, k);
  for (const a of d.areas) for (const k of ["id", "footprint", "position", "on_hand_lbs", "pallets", "utilisation"]) assert.ok(k in a, k);
  assert.ok(d.areas.some((a) => a.id === "kitchen"));
});
