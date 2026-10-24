// public/twin.usda places the captures with the viewer's twin.config.json placements, so the
// OpenUSD twin, the browser and the operations layer share one frame. Re-run
// scripts/align_twin_usd.py after changing a capture's position or yaw_deg.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (p) => readFileSync(new URL(`../public/${p}`, import.meta.url), "utf8");
const usda = read("twin.usda"), cfg = JSON.parse(read("twin.config.json"));
// Like the viewer: a zone trained from video (photoreal.json) is placed by its `photoreal` block.
const trained = new Set(JSON.parse(read(cfg.photoreal || "zones/photoreal.json")).zones.map((z) => z.name));

test("twin.usda placements match twin.config.json", () => {
  for (const [name, entry] of Object.entries(cfg.captures)) {
    const c = entry.photoreal && trained.has(name) ? entry.photoreal : entry;
    const block = usda.match(new RegExp(`def Xform "${name}"[\\s\\S]*?\\n        }`))?.[0];
    assert.ok(block, `${name} missing from twin.usda`);
    const t = block.match(/xformOp:translate = \(([^)]+)\)/)[1].split(",").map(Number);
    const yaw = Number(block.match(/xformOp:rotateY = (-?[\d.]+)/)[1]);
    assert.deepEqual(t, c.position, name);
    assert.equal(yaw, c.yaw_deg ?? 0, name);
  }
});

test("twin.usda layers the operations prims and the crop overrides", () => {
  assert.match(usda, /@\.\/operations\.usda@<\/World\/Operations>/);
  assert.match(usda, /subLayers = \[\s*@\.\/twin_crop\.usdc@/);
});
