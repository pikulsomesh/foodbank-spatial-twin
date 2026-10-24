// Operational areas drawn on the twin's floor: an outline per area from ops/operations.json (the
// same footprints GET /operations reports). Areas outside the generated building (the community
// kitchen annex) get a floor slab and walls in the building's style around their capture: a
// full-height back wall and low cutaway walls on the open sides. `stations` adds floor marks where
// the kitchen equipment stands, for when no kitchen capture is loaded.
import * as THREE from "three";

const KIND_COLOR = { dock: 0x9aa5b1, sorting: 0xd95926, storage: 0x3987e5, staging: 0xc98500, market: 0x199e70, kitchen: 0xd55181 };

const WALL_HEIGHT = 7, CUTAWAY = 1.2, T = 0.15;

export function addOpsOverlay(scene, zones, { annex = ["kitchen"], stations = [] } = {}) {
  const group = new THREE.Group();
  group.name = "operations-overlay";
  for (const z of zones) {
    const [x0, x1] = z.footprint.x, [z0, z1] = z.footprint.z;
    const color = KIND_COLOR[z.kind] ?? 0xffffff;
    const pts = [[x0, z0], [x1, z0], [x1, z1], [x0, z1]].map(([x, zz]) => new THREE.Vector3(x, 0.06, zz));
    const line = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(pts),
      new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.8, depthWrite: false }));
    line.userData.zone = z.id;
    group.add(line);
    if (annex.includes(z.id)) {
      const floor = new THREE.MeshStandardMaterial({ color: 0x8f9499, roughness: 0.9 });
      const wall = new THREE.MeshStandardMaterial({ color: 0xd5d9dd, roughness: 0.85 });
      const box = (w, h, d, x, y, zz, mat) => { const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat); m.position.set(x, y, zz); group.add(m); };
      const cx = (x0 + x1) / 2, cz = (z0 + z1) / 2;
      box(x1 - x0, 0.02, z1 - z0, cx, -0.01, cz, floor);                          // slab, just under the capture's floor
      box(x1 - x0 + T, WALL_HEIGHT, T, cx, WALL_HEIGHT / 2, z0 - T / 2, wall);   // back wall, in line with the building's
      box(T, CUTAWAY, z1 - z0, x1 + T / 2, CUTAWAY / 2, cz, wall);              // east cutaway
      box(x1 - x0 + T, CUTAWAY, T, cx, CUTAWAY / 2, z1 + T / 2, wall);          // front cutaway
      box(T, CUTAWAY, z1 - z0, x0 - T / 2, CUTAWAY / 2, cz, wall);              // west cutaway, facing the building
    }
  }
  // Kitchen stations as floor marks where the equipment stands (positions from the ops data).
  const mark = new THREE.LineBasicMaterial({ color: KIND_COLOR.kitchen, transparent: true, opacity: 0.9, depthWrite: false });
  for (const st of stations) {
    const [x, , z] = st.position, w = 0.5 + 0.2 * Math.min(st.count, 4);
    const pts = [[x - w, z - 0.7], [x + w, z - 0.7], [x + w, z + 0.7], [x - w, z + 0.7]].map(([a, b]) => new THREE.Vector3(a, 0.07, b));
    group.add(new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(pts), mark));
  }
  scene.add(group);
  return group;
}
