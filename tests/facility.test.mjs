// public/facility.glb (scripts/build_facility.py) is the viewer's default scene. It must carry
// every operational area and the ForkliftRig node the viewer animates, in the telemetry frame.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";

const pub = (p) => new URL(`../public/${p}`, import.meta.url);
const cfg = JSON.parse(readFileSync(pub("twin.config.json"), "utf8"));
const model = cfg.model || "facility.glb";

function gltfJson(buf) {
  assert.equal(buf.toString("ascii", 0, 4), "glTF");
  const len = buf.readUInt32LE(12);
  assert.equal(buf.toString("ascii", 16, 20), "JSON");
  return JSON.parse(buf.toString("utf8", 20, 20 + len));
}

test("facility model and its OpenUSD twin are published", () => {
  assert.ok(existsSync(pub(model)), model);
  assert.ok(existsSync(pub(cfg.modelUsd || "facility.usdz")));
  assert.match(readFileSync(pub("twin.usda"), "utf8"), /@\.\/facility\.usdz@<\/Facility>/);
});

test("facility.glb has every area and the forklift rig", () => {
  const g = gltfJson(readFileSync(pub(model)));
  const names = new Set(g.nodes.map((n) => n.name));
  for (const n of ["Shell", "Freezer", "Cooler", "DryStorage", "Staging", "Receiving", "Sorting", "Market", "Kitchen", "ForkliftRig"]) {
    assert.ok(names.has(n), `missing node ${n}`);
  }
  // Floor of the building spans the telemetry frame's x 0-26, z 0-20.
  const floor = g.nodes.find((n) => n.name === "Shell_floor");
  const pos = g.accessors[g.meshes[floor.mesh].primitives[0].attributes.POSITION];
  assert.deepEqual([pos.min[0], pos.min[2], pos.max[0], pos.max[2]].map((v) => Math.round(v)), [0, 0, 26, 20]);
});
