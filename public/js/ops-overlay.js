// Operational areas drawn on the twin's floor: an outline per area from ops/operations.json (the
// same footprints GET /operations reports), and a floor slab for areas no capture or schematic
// covers yet, such as the community kitchen annex, so they read as part of the building, with
// floor marks where its stations stand until the kitchen capture lands.
import * as THREE from "three";

const KIND_COLOR = { dock: 0x9aa5b1, sorting: 0xd95926, storage: 0x3987e5, staging: 0xc98500, market: 0x199e70, kitchen: 0xd55181 };

export function addOpsOverlay(scene, zones, { uncovered = ["kitchen"], stations = [] } = {}) {
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
    if (uncovered.includes(z.id)) {
      const slab = new THREE.Mesh(new THREE.PlaneGeometry(x1 - x0, z1 - z0).rotateX(-Math.PI / 2),
        new THREE.MeshStandardMaterial({ color: 0x8f9499, roughness: 0.9 }));
      slab.position.set((x0 + x1) / 2, 0.01, (z0 + z1) / 2);
      group.add(slab);
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
