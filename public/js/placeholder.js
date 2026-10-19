// Procedural stand-in for the World Labs twin, built from Gaussian splats so it renders through
// the same Spark path as the real factory.spz. Layout follows the telemetry frame in
// src/telemetry/app.py (metres, Y up, origin at the dock-door corner): freezer and cooler along
// the back wall, pallet racking around the dry-storage aisle at x=20, dock doors on the x=0 wall,
// a volunteer sorting area and donation staging at the front. It is a plausible food bank, not
// a replica of any real building.
import * as THREE from "three";

export const BOUNDS = { x: [0, 26], z: [0, 20], wallHeight: 7 };

function mulberry32(seed) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// density > 1 means coarser sampling (fewer splats) for phones.
// `clear` ({ x: [x0, x1], z: [z0, z1] }) leaves out racking there, to make room for a captured splat.
export function buildPlaceholder(splats, { density = 1, clear = null } = {}) {
  const rand = mulberry32(7);
  const c = new THREE.Vector3(), s = new THREE.Vector3(), q = new THREE.Quaternion(), col = new THREE.Color();
  const OTHER = { x: ["y", "z"], y: ["x", "z"], z: ["x", "y"] };

  const setHex = (hex, vary) => {
    const k = 1 + (rand() - 0.5) * vary;
    col.r = Math.min(1, (((hex >> 16) & 255) / 255) * k);
    col.g = Math.min(1, (((hex >> 8) & 255) / 255) * k);
    col.b = Math.min(1, ((hex & 255) / 255) * k);
  };

  // Axis-aligned rectangle of flat splats. `color` is a hex or (x, y, z) => hex.
  function rect(n, at, r0, r1, step, color, { vary = 0.08, opacity = 1 } = {}) {
    step *= density;
    const [a, b] = OTHER[n];
    for (let i = r0[0] + step / 2; i < r0[1]; i += step) {
      for (let j = r1[0] + step / 2; j < r1[1]; j += step) {
        c[n] = at;
        c[a] = Math.min(r0[1], i + (rand() - 0.5) * step * 0.3);
        c[b] = Math.min(r1[1], j + (rand() - 0.5) * step * 0.3);
        s[n] = 0.01; s[a] = step * 0.62; s[b] = step * 0.62;
        setHex(typeof color === "function" ? color(c.x, c.y, c.z) : color, vary);
        splats.pushSplat(c, s, q, opacity, col);
      }
    }
  }

  // Box from min to max; `skip` lists faces to leave open ("-y" is the bottom, and so on).
  function box(min, max, step, color, { skip = ["-y"], ...opts } = {}) {
    const faces = [["x", min.x, "-x"], ["x", max.x, "+x"], ["y", min.y, "-y"], ["y", max.y, "+y"], ["z", min.z, "-z"], ["z", max.z, "+z"]];
    for (const [n, at, name] of faces) {
      if (skip.includes(name)) continue;
      const [a, b] = OTHER[n];
      rect(n, at, [min[a], max[a]], [min[b], max[b]], step, color, opts);
    }
  }
  const v = (x, y, z) => ({ x, y, z });

  // Floor: polished concrete with yellow lane lines either side of the forklift route.
  const lanes = [[[1, 12], [4, 6]], [[4, 6], [12, 6]], [[12, 6], [20, 10]], [[20, 10], [20, 3]], [[20, 10], [12, 12]], [[12, 12], [1, 12]]];
  const segDist = (x, z, [[ax, az], [bx, bz]]) => {
    const dx = bx - ax, dz = bz - az;
    const t = Math.max(0, Math.min(1, ((x - ax) * dx + (z - az) * dz) / (dx * dx + dz * dz)));
    return Math.hypot(x - ax - t * dx, z - az - t * dz);
  };
  rect("y", 0, [0, 26], [0, 20], 0.11, (x, _y, z) => {
    const d = Math.min(...lanes.map((l) => segDist(x, z, l)));
    if (d > 1.15 && d < 1.27) return 0xe8b923;
    if (x > 1 && x < 7 && z < 5.5) return 0xc9d3dc; // freezer floor
    if (x > 9 && x < 15 && z < 5.5) return 0xc4d4d2; // cooler floor
    return (Math.floor(x / 4) + Math.floor(z / 4)) % 2 ? 0x8e9196 : 0x878a8f; // slab joints
  }, { vary: 0.07 });

  // Walls: full-height back (z=0) and dock (x=0) walls, low cutaway walls on the open sides.
  const panel = (x, y, z) => ((Math.floor((x + z) / 1.2) % 2) ? 0xd9dde2 : 0xcfd4da);
  rect("z", 0, [0, 26], [0, BOUNDS.wallHeight], 0.16, panel);
  rect("x", 0, [0, BOUNDS.wallHeight], [0, 20], 0.16, (x, y, z) => {
    const door = [8.6, 11.6, 14.6].some((d) => z > d && z < d + 2.4) && y < 3.4;
    if (door) return Math.floor(y / 0.25) % 2 ? 0x4d6b8a : 0x45627f; // roll-up dock doors
    return y < 1.1 ? 0xe2a92b : panel(x, y, z); // yellow dock bumper band
  });
  rect("x", 26, [0, 1.2], [0, 20], 0.14, panel);
  rect("z", 20, [0, 26], [0, 1.2], 0.14, panel);

  // Freezer and cooler: insulated panel rooms with strip-curtain doors facing the aisle.
  const room = (x0, x1, tint, doorTint) => {
    box(v(x0, 0, 0.05), v(x1, 4, 5.5), 0.12, (x, y, z) => {
      if (z > 5.45 && Math.abs(x - (x0 + x1) / 2) < 0.9 && y < 2.8) return Math.floor(x / 0.15) % 2 ? doorTint : 0xb8c7d1;
      if (y > 3.95) return 0xa9b4bd;
      return Math.floor(x / 1.1) % 2 ? tint : 0xeef2f5;
    }, { skip: ["-y", "-z"] });
  };
  room(1, 7, 0xe3ecf4, 0x7fb2d9);
  room(9, 15, 0xe4f0ee, 0x6fc2b5);

  // Pallet loads: shrink-wrapped pallets or stacks of cartons.
  const CARTONS = [0xb98a5a, 0xc89b67, 0xa77a4c, 0xd4b083, 0x9c6b43];
  function pallet(x, y, z, w, d) {
    box(v(x, y, z), v(x + w, y + 0.14, z + d), 0.07, 0x8a6a45, { skip: [] });
    const h = 0.8 + rand() * 0.55;
    if (rand() < 0.4) {
      box(v(x + 0.04, y + 0.14, z + 0.04), v(x + w - 0.04, y + 0.14 + h, z + d - 0.04), 0.07, 0xdfe5ea, { vary: 0.18, opacity: 0.95 });
    } else {
      const hex = CARTONS[Math.floor(rand() * CARTONS.length)];
      box(v(x + 0.04, y + 0.14, z + 0.04), v(x + w - 0.04, y + 0.14 + h, z + d - 0.04), 0.07,
        (px, py) => (Math.floor((py - y) / 0.3) % 2 ? hex : hex - 0x0b0b0b), { vary: 0.12 });
    }
  }

  // Selective pallet racking: red uprights, grey beams, 3 bays deep in z, 4 levels.
  function rackRow(x0, depth, z0, z1) {
    if (clear && x0 < clear.x[1] && x0 + depth > clear.x[0] && z0 < clear.z[1] && z1 > clear.z[0]) return;
    const levels = [0, 1.7, 3.4, 5.1], top = 6.4, bays = Math.round((z1 - z0) / 2.7);
    const bayLen = (z1 - z0) / bays;
    for (let b = 0; b <= bays; b++) {
      const z = z0 + b * bayLen;
      for (const x of [x0, x0 + depth - 0.09]) box(v(x, 0, z - 0.045), v(x + 0.09, top, z + 0.045), 0.045, 0xb3262b, { skip: [] });
    }
    for (const y of levels.slice(1)) {
      for (const x of [x0, x0 + depth - 0.06]) box(v(x, y - 0.12, z0), v(x + 0.06, y, z1), 0.05, 0x5d6670, { skip: [] });
    }
    for (let b = 0; b < bays; b++) {
      for (const y of levels) {
        if (rand() < 0.12) continue; // a few empty slots read as a working warehouse
        const z = z0 + b * bayLen;
        pallet(x0 + 0.1, y, z + 0.12, depth - 0.2, bayLen / 2 - 0.2);
        if (rand() < 0.85) pallet(x0 + 0.1, y, z + bayLen / 2 + 0.08, depth - 0.2, bayLen / 2 - 0.2);
      }
    }
  }
  rackRow(17.4, 1.2, 1.0, 8.5);
  rackRow(21.4, 1.2, 1.0, 8.5);
  rackRow(24.6, 1.2, 1.0, 8.5);

  // Volunteer sorting area: two lines of tables with roller conveyors and produce bins.
  for (const z of [15.2, 17.6]) {
    box(v(7, 0.85, z), v(15.5, 0.95, z + 1.0), 0.06, (x) => (Math.floor(x / 0.12) % 2 ? 0x9aa3ab : 0x7d868e), { skip: [] });
    for (let x = 7.2; x < 15.5; x += 2.1) box(v(x, 0, z + 0.1), v(x + 0.06, 0.85, z + 0.16), 0.04, 0x5b6268, { skip: [] });
    for (let x = 7.5; x < 14.9; x += 1.4) {
      const hex = [0x3f7f4f, 0xc7562f, 0xd9a43a, 0x5a8fc2][Math.floor(rand() * 4)];
      box(v(x, 0.95, z + 0.2), v(x + 0.55, 1.3, z + 0.8), 0.05, hex, { vary: 0.2 });
    }
  }

  // Donation staging by the docks: loose pallets waiting to be sorted or put away.
  for (let i = 0; i < 9; i++) pallet(2 + (i % 3) * 1.4, 0, 16.2 + Math.floor(i / 3) * 1.25, 1.1, 1.0);
  for (let i = 0; i < 6; i++) pallet(16.5 + (i % 3) * 1.5, 0, 14 + Math.floor(i / 3) * 1.4, 1.1, 1.1);

  return splats.numSplats;
}
