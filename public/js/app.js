// Browser viewer for the food bank twin: Spark renders the splat, HTML badges and a few meshes
// show telemetry from the AWS API (or the local simulator when the API is unreachable).
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { SparkRenderer, SplatMesh } from "@sparkjsdev/spark";
import { buildPlaceholder } from "./placeholder.js";
import { startFeed, simulate, forklift as simForklift } from "./telemetry.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);

// ?record=1 freezes wall-clock time: the page renders only when a capture script calls
// window.twinStep(ms), so a slow (software-rendered) browser still produces a smooth video.
const recording = params.has("record");
const epoch = Date.now();
let virtualMs = 0;
const nowMs = () => (recording ? virtualMs : performance.now());
const wallMs = () => (recording ? epoch + virtualMs : Date.now());
const phone = matchMedia("(max-width: 720px)").matches || navigator.maxTouchPoints > 1 && Math.min(screen.width, screen.height) < 820;

const DEFAULTS = { zones: "zones/zones.json", splat: "factory.spz", meta: "factory.meta.json", usdz: "factory.usdz",
  captures: {}, telemetryApi: "" };

async function getJSON(url) {
  try { const r = await fetch(url, { cache: "no-cache" }); return r.ok ? await r.json() : null; } catch { return null; }
}

// ---------- Panel and badges (work even without WebGL) ----------
const cards = new Map(), badges = new Map();
let selected = null, lastData = null, lastSource = "simulated";
const fmtT = (v) => `${v > 0 ? "" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(1)}°C`;

function card(container, id, onClick) {
  let el = cards.get(id);
  if (!el) {
    el = document.createElement("button");
    el.className = "card";
    el.addEventListener("click", () => onClick(id));
    container.append(el);
    cards.set(id, el);
  }
  return el;
}

function badge(id, cls, onClick) {
  let el = badges.get(id);
  if (!el) {
    el = document.createElement("div");
    el.className = `badge ${cls}`;
    el.addEventListener("click", () => onClick(id));
    $("badges").append(el);
    badges.set(id, el);
  }
  return el;
}

function setFeed(source, data) {
  const pill = $("feed-pill");
  pill.classList.toggle("live", source === "live");
  pill.classList.toggle("sim", source !== "live");
  const simulatedBackend = source === "live" && data.simulated;
  pill.querySelector("span").textContent = source === "live"
    ? (simulatedBackend ? "Live from AWS (simulated sensors)" : "Live from AWS")
    : "Simulated feed (offline)";
  pill.title = source === "live"
    ? "Readings come from the AWS telemetry API every 2 seconds."
    : "The AWS API isn't reachable from here, so the page runs the same sensor model locally.";
}

function render(data, source) {
  lastData = data; lastSource = source;
  setFeed(source, data);
  $("stamp").textContent = new Date(data.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

  for (const z of data.zones) {
    const el = card($("zones"), z.id, select);
    el.className = `card ${z.status}${selected === z.id ? " sel" : ""}`;
    el.innerHTML = `<div class="row"><span class="name">${z.label}</span><span class="tag ${z.status}">${z.status === "ok" ? "OK" : z.status === "warn" ? "Drifting" : "Alarm"}</span></div>
      <div class="row"><span class="big">${fmtT(z.temperature_c)}</span><span class="meta">set ${fmtT(z.setpoint_c)}</span></div>
      <div class="meta">${z.humidity_pct.toFixed(0)}% RH · door ${z.door_open ? "open" : "closed"}</div>`;
    const b = badge(z.id, "zone", select);
    b.className = `badge zone ${z.status}`;
    b.innerHTML = `<i class="dot"></i>${z.label} <b>${fmtT(z.temperature_c)}</b>${z.door_open ? '<span class="door">door open</span>' : ""}`;
    scene3d?.zoneStatus(z);
  }
  for (const f of data.forklifts) {
    const el = card($("fleet"), f.id, select);
    el.className = `card${selected === f.id ? " sel" : ""}`;
    el.innerHTML = `<div class="row"><span class="name">${f.label}</span><span class="meta">${f.speed_mps.toFixed(2)} m/s</span></div>
      <div class="meta">Battery ${f.battery_pct.toFixed(0)}% · heading ${f.heading_deg.toFixed(0)}°</div>
      <div class="bar"><i style="width:${f.battery_pct}%"></i></div>`;
    const b = badge(f.id, "fork", select);
    b.innerHTML = `<i class="dot"></i>${f.label} <b>${f.battery_pct.toFixed(0)}%</b>`;
    scene3d?.forkliftTarget(f, source);
  }
  const alerts = data.alerts || [];
  $("alerts-wrap").hidden = !alerts.length;
  $("alerts").innerHTML = alerts.map((a) => `<li>${a}</li>`).join("");
  const worst = data.zones.find((z) => z.status === "alarm") || data.zones.find((z) => z.status === "warn");
  $("panel-summary").textContent = worst ? `${worst.label} ${fmtT(worst.temperature_c)} · ${worst.status === "alarm" ? "alarm" : "drifting"}`
    : `All ${data.zones.length} zones in range`;
}

function select(id) {
  selected = selected === id ? null : id;
  for (const [k, el] of cards) el.classList.toggle("sel", k === selected);
  if (selected) scene3d?.focus(selected);
}

$("panel-toggle").addEventListener("click", () => {
  const p = $("panel"), collapsed = p.classList.toggle("collapsed");
  $("panel-toggle").setAttribute("aria-expanded", String(!collapsed));
});
if (phone) { $("panel").classList.add("collapsed"); $("panel-toggle").setAttribute("aria-expanded", "false"); }

// ---------- 3D scene ----------
let scene3d = null;

function hasWebGL2() {
  try { return !!document.createElement("canvas").getContext("webgl2"); } catch { return false; }
}

function makeForklift() {
  const g = new THREE.Group();
  const yellow = new THREE.MeshStandardMaterial({ color: 0xf5c518, roughness: 0.55 });
  const dark = new THREE.MeshStandardMaterial({ color: 0x23272b, roughness: 0.8 });
  const steel = new THREE.MeshStandardMaterial({ color: 0x8a9299, metalness: 0.6, roughness: 0.4 });
  const add = (geo, mat, x, y, z) => { const m = new THREE.Mesh(geo, mat); m.position.set(x, y, z); g.add(m); return m; };
  add(new THREE.BoxGeometry(1.1, 0.75, 1.7), yellow, 0, 0.6, -0.1);           // body
  add(new THREE.BoxGeometry(1.0, 0.5, 0.5), dark, 0, 0.75, -0.75);             // counterweight
  for (const x of [-0.5, 0.5]) for (const z of [-0.45, 0.55]) add(new THREE.BoxGeometry(0.06, 1.3, 0.06), dark, x, 1.6, z); // guard posts
  add(new THREE.BoxGeometry(1.1, 0.05, 1.1), dark, 0, 2.25, 0.05);             // overhead guard
  for (const x of [-0.3, 0.3]) add(new THREE.BoxGeometry(0.08, 2.4, 0.1), steel, x, 1.25, 0.85); // mast
  for (const x of [-0.25, 0.25]) add(new THREE.BoxGeometry(0.12, 0.05, 1.0), steel, x, 0.12, 1.4); // forks
  const wheel = new THREE.CylinderGeometry(0.25, 0.25, 0.2, 16).rotateZ(Math.PI / 2);
  for (const x of [-0.55, 0.55]) for (const z of [-0.6, 0.5]) add(wheel, dark, x, 0.25, z);
  const ring = new THREE.Mesh(new THREE.RingGeometry(1.4, 1.55, 48).rotateX(-Math.PI / 2),
    new THREE.MeshBasicMaterial({ color: 0xf5c518, transparent: true, opacity: 0.55, depthWrite: false }));
  ring.position.y = 0.03;
  g.add(ring);
  return g;
}

const STATUS_COLOR = { ok: 0x4cc38a, warn: 0xf0b429, alarm: 0xf05252 };

async function initScene(cfg) {
  const stage = $("stage");
  const renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(devicePixelRatio, phone ? 1.5 : 2));
  renderer.setSize(innerWidth, innerHeight);
  renderer.setClearColor(0x0f1720);
  stage.append(renderer.domElement);

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0x0f1720, 45, 90);
  const camera = new THREE.PerspectiveCamera(phone ? 60 : 50, innerWidth / innerHeight, 0.1, 200);
  const spark = new SparkRenderer({ renderer });
  scene.add(spark);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(10, 20, 15);
  scene.add(sun);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.maxPolarAngle = Math.PI * 0.49;
  controls.minDistance = 2;
  controls.maxDistance = 60;
  controls.autoRotateSpeed = 0.6;

  // Twin: the World Labs captures placed into the procedural building, or the building alone.
  const loadingText = $("loading-text");
  const captures = params.has("placeholder") ? [] : await loadCaptures(cfg, scene, loadingText);
  const withContext = !captures.length || cfg.context !== false;
  if (withContext) {
    loadingText.textContent = "Building the facility layout…";
    const clear = captures.map((c) => c.clear).filter(Boolean);
    const massing = captures.length > 0 && cfg.context?.style !== "colour";
    const mesh = new SplatMesh({ constructSplats: (splats) => { buildPlaceholder(splats, { density: phone ? 1.45 : 1, clear, massing }); } });
    scene.add(mesh);
    await mesh.initialized;
  }
  const pill = $("twin-pill");
  if (captures.length) {
    const names = captures.map((c) => c.title || c.name);
    const list = names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}` : names[0];
    pill.textContent = withContext ? `${captures.length} captured zone${captures.length > 1 ? "s" : ""} + layout` : "World Labs capture";
    pill.title = "Gaussian splats from World Labs world models, built from the walkthrough video.";
    const trained = captures.filter((c) => c.trained).map((c) => c.title || c.name);
    $("about-text").textContent = `The ${list} ${names.length > 1 ? "are" : "is a"} Gaussian splat${names.length > 1 ? "s" : ""} built from walkthrough video.` +
      (trained.length ? ` The ${trained.join(" and ")} ${trained.length > 1 ? "were" : "was"} trained on dozens of video frames (3D Gaussian Splatting); the others are fused from a few frames posed by a World Labs world model.` : " Frames were posed and fused with World Labs world models.") +
      (withContext ? " The rest of the building is a schematic layout in the same metric frame, until more of the facility is captured." : "");
  } else {
    pill.textContent = "Placeholder layout";
    pill.title = "Procedural splat stand-in until the World Labs capture is published.";
  }
  if (cfg.usdz) fetch(cfg.usdz, { method: "HEAD" }).then((r) => {
    if (r.ok && !(r.headers.get("content-type") || "").includes("text/html")) { $("usdz-link").href = cfg.usdz; $("usdz-link").hidden = false; }
  }).catch(() => {});

  // Telemetry markers
  const zoneRings = new Map();
  const ringGeo = new THREE.RingGeometry(0.9, 1.15, 48).rotateX(-Math.PI / 2);
  const fork = makeForklift();
  scene.add(fork);
  const forkTarget = { pos: new THREE.Vector3(1, 0, 12), yaw: 0, live: false };
  const anchors = new Map(); // id -> Vector3 where the badge points

  // On wide screens the panel covers the right edge, so centre the view in the space left of it.
  function fit() {
    const side = innerWidth > 720 ? $("panel").offsetWidth + 16 : 0;
    camera.aspect = (innerWidth + side) / innerHeight;
    if (side) camera.setViewOffset(innerWidth + side, innerHeight, side, 0, innerWidth, innerHeight);
    else camera.clearViewOffset();
    camera.updateProjectionMatrix();
    renderer.setSize(innerWidth, innerHeight);
  }
  fit();
  addEventListener("resize", fit);

  // Overview backs off until the 26 m building fits the screen width (portrait phones need more room).
  const overview = () => {
    const halfW = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * Math.min(camera.aspect, 1.6);
    const d = THREE.MathUtils.clamp(16 / halfW, 34, 62);
    const dir = new THREE.Vector3(0.05, camera.aspect < 1 ? 0.78 : 0.55, 1).normalize();
    return [dir.multiplyScalar(d).add(new THREE.Vector3(13, 0, 9)).toArray(), [13, 0, camera.aspect < 1 ? 7 : 8.5]];
  };
  // Camera presets in the telemetry frame.
  const VIEWS = {
    get overview() { return overview(); },
    cold: [[8, 7, 15], [8, 1.5, 3]],
    aisle: [[20, 2, 13], [20, 1.5, 5]],
  };
  // `photo`: a capture seen from its source camera, which the forklift would otherwise drive through.
  let follow = false, photo = false, tween = null;
  // A view is [position, target, fov?]. Source-camera views carry the video's narrow lens; widen it
  // on tall screens so the view still spans what the frame showed side to side.
  const baseFov = camera.fov;
  function viewFov(fov) {
    if (!fov) return baseFov;
    const half = Math.atan(Math.tan(THREE.MathUtils.degToRad(fov / 2)) * (1920 / 816));
    return Math.max(fov, THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(half) / camera.aspect)));
  }
  function goTo(pos, target, ms = 1200, fov) {
    tween = { t0: nowMs(), ms, p0: camera.position.clone(), q0: controls.target.clone(), f0: camera.fov, f1: viewFov(fov),
      p1: new THREE.Vector3(...pos), q1: new THREE.Vector3(...target) };
  }
  const goView = (name, ms) => { const [p, t, f] = VIEWS[name]; goTo(p, t, ms, f); };
  camera.position.set(...VIEWS.overview[0]).add(new THREE.Vector3(6, 8, 10));
  controls.target.set(...VIEWS.overview[1]);
  // One button per capture, looking the way its source cameras looked (-Z in the capture frame).
  if (captures.length) {
    document.querySelector('#views [data-view="aisle"]')?.remove();
    const followBtn = document.querySelector('#views [data-view="follow"]');
    captures.forEach((c, i) => {
      const yaw = THREE.MathUtils.degToRad(c.yaw_deg || 0), dir = new THREE.Vector3(-Math.sin(yaw), 0, -Math.cos(yaw));
      const o = new THREE.Vector3(...c.position);
      const src = c.views?.[c.source_view ?? 0];
      const place = (p) => new THREE.Vector3(...p).applyAxisAngle(new THREE.Vector3(0, 1, 0), yaw).add(o).toArray();
      VIEWS[`capture-${i}`] = c.view || (src ? [place(src.position), place(src.target), src.fov_deg]
        : [o.clone().setY(o.y + 1.6).toArray(), o.clone().addScaledVector(dir, 7).setY(o.y + 1.3).toArray()]);
      const btn = document.createElement("button");
      btn.dataset.view = `capture-${i}`;
      btn.textContent = c.button || c.name;
      btn.title = c.label || "";
      followBtn.before(btn);
    });
  }
  // ?cam=px,py,pz,tx,ty,tz pins the camera, for screenshots and recordings.
  const pinned = (params.get("cam") || "").split(",").map(Number);
  if (pinned.length === 6 && pinned.every(Number.isFinite)) {
    camera.position.set(...pinned.slice(0, 3));
    controls.target.set(...pinned.slice(3));
  } else {
    // Land on the first capture when there is one: seen from its own cameras it looks like the video.
    const first = captures.length && !params.has("overview") ? "capture-0" : "overview";
    photo = first.startsWith("capture-");
    document.querySelectorAll("#views [data-view]").forEach((b) => b.classList.toggle("on", b.dataset.view === first));
    goView(first, 2600);
  }

  for (const btn of document.querySelectorAll("#views [data-view]")) {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#views [data-view]").forEach((b) => b.classList.toggle("on", b === btn));
      follow = btn.dataset.view === "follow";
      photo = btn.dataset.view.startsWith("capture-");
      if (!follow) goView(btn.dataset.view);
      else if (camera.fov !== baseFov) { camera.fov = baseFov; camera.updateProjectionMatrix(); }
    });
  }
  const tourBtn = $("tour");
  const setTour = (on) => { controls.autoRotate = on; tourBtn.setAttribute("aria-pressed", String(on)); };
  tourBtn.addEventListener("click", () => setTour(!controls.autoRotate));
  if (params.has("tour")) setTour(true);
  controls.addEventListener("start", () => { tween = null; });


  const v = new THREE.Vector3();
  let last = nowMs();
  function frame() {
    const now = nowMs(), dt = Math.min((now - last) / 1000, 0.1);
    last = now;

    // Forklift: exact pose from the local model when simulated, smoothed toward readings when live.
    if (!forkTarget.live) {
      const f = simForklift(wallMs() / 1000);
      forkTarget.pos.fromArray(f.position);
      forkTarget.yaw = THREE.MathUtils.degToRad(f.heading_deg);
      fork.position.copy(forkTarget.pos);
      fork.rotation.y = forkTarget.yaw;
    } else {
      const k = 1 - Math.exp(-dt * 3);
      fork.position.lerp(forkTarget.pos, k);
      let d = forkTarget.yaw - fork.rotation.y;
      d = Math.atan2(Math.sin(d), Math.cos(d));
      fork.rotation.y += d * k;
    }
    fork.visible = !photo;
    if (photo) anchors.delete("forklift-1");
    else anchors.set("forklift-1", v.copy(fork.position).setY(2.6).clone());

    for (const [, ring] of zoneRings) ring.material.opacity = ring.userData.alarm ? 0.45 + 0.35 * Math.sin(now / 180) : 0.5;

    if (tween) {
      const t = Math.min(1, (now - tween.t0) / tween.ms), e = t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
      camera.position.lerpVectors(tween.p0, tween.p1, e);
      controls.target.lerpVectors(tween.q0, tween.q1, e);
      if (tween.f0 !== tween.f1) { camera.fov = tween.f0 + (tween.f1 - tween.f0) * e; camera.updateProjectionMatrix(); }
      if (t === 1) tween = null;
    } else if (follow) {
      const behind = new THREE.Vector3(Math.sin(fork.rotation.y), 0, Math.cos(fork.rotation.y)).multiplyScalar(-6);
      const want = fork.position.clone().add(behind).setY(4.5);
      camera.position.lerp(want, 1 - Math.exp(-dt * 2));
      controls.target.lerp(fork.position.clone().setY(1), 1 - Math.exp(-dt * 4));
    }
    controls.update(dt);
    renderer.render(scene, camera);

    // Pin HTML badges to their anchors.
    for (const [id, el] of badges) {
      const a = anchors.get(id);
      if (!a) { el.classList.add("hidden"); continue; }
      v.copy(a).project(camera);
      const off = v.z > 1 || Math.abs(v.x) > 1.1 || Math.abs(v.y) > 1.1;
      el.classList.toggle("hidden", off);
      if (!off) el.style.transform = `translate(${(v.x * 0.5 + 0.5) * innerWidth}px, ${(-v.y * 0.5 + 0.5) * innerHeight}px) translate(-50%, -100%)`;
    }
  }
  if (recording) {
    let nextReading = 0;
    const tick = () => new Promise((r) => requestAnimationFrame(() => r()));
    // Spark sorts splats in a worker, so give it a few real frames to catch up with the camera
    // before the frame counts.
    window.twinStep = async (ms, settle = 1) => {
      virtualMs += ms;
      if (virtualMs >= nextReading) { render(simulate(wallMs()), "simulated"); nextReading += 2000; }
      for (let i = 0; i < settle; i++) { frame(); await tick(); }
      frame();
    };
  } else {
    renderer.setAnimationLoop(frame);
  }

  return {
    zoneStatus(z) {
      let ring = zoneRings.get(z.id);
      if (!ring) {
        ring = new THREE.Mesh(ringGeo, new THREE.MeshBasicMaterial({ transparent: true, depthWrite: false }));
        ring.position.set(z.position[0], 0.04, z.position[2]);
        scene.add(ring);
        zoneRings.set(z.id, ring);
      }
      ring.material.color.setHex(STATUS_COLOR[z.status] || STATUS_COLOR.ok);
      ring.userData.alarm = z.status === "alarm";
      anchors.set(z.id, new THREE.Vector3(z.position[0], Math.max(z.position[1], 0) + 2.9, z.position[2]));
    },
    forkliftTarget(f, source) {
      forkTarget.live = source === "live";
      if (forkTarget.live) {
        forkTarget.pos.fromArray(f.position);
        forkTarget.yaw = THREE.MathUtils.degToRad(f.heading_deg);
      }
    },
    focus(id) {
      const a = anchors.get(id);
      if (!a) return;
      follow = id.startsWith("forklift");
      photo = false;
      document.querySelectorAll("#views [data-view]").forEach((b) => b.classList.toggle("on", b.dataset.view === (follow ? "follow" : "")));
      if (!follow) goTo([a.x - 2, a.y + 4, a.z + 9], [a.x, a.y - 1.5, a.z]);
    },
  };
}

async function fetchSplat(url) {
  try {
    const r = await fetch(url);
    if (!r.ok || (r.headers.get("content-type") || "").includes("text/html")) return null;
    return new Uint8Array(await r.arrayBuffer());
  } catch { return null; }
}

// Which captures to show: every zone in zones.json (written by the pipeline) that twin.config.json
// knows where to put, or else the single factory.spz as the racking aisle. A zone that also has a
// trained 3DGS in photoreal.json uses it (placed by its entry's `photoreal` block) unless ?atlas is set.
async function captureList(cfg) {
  const placed = cfg.captures || {};
  const [zj, pj] = await Promise.all([cfg.zones && getJSON(cfg.zones), cfg.photoreal && !params.has("atlas") && getJSON(cfg.photoreal)]);
  const trained = new Map((pj?.zones || []).map((z) => [z.name, z]));
  // Zones trained from video with no Atlas capture (the kitchen) only exist in photoreal.json.
  const atlasNames = new Set((zj?.zones || []).map((z) => z.name));
  const trainedOnly = [...trained.values()].filter((t) => !atlasNames.has(t.name) && placed[t.name]?.photoreal);
  if (zj?.zones?.length || trainedOnly.length) {
    return [...(zj?.zones || []), ...trainedOnly]
      .filter((z) => placed[z.name] || console.info(`No placement for captured zone ${z.name}; skipping it.`))
      .map((z) => {
        const { photoreal, ...atlas } = placed[z.name];
        const t = photoreal && trained.get(z.name);
        const src = t || z;
        return { name: z.name, label: z.label, url: src.url, ground: Number(src.ground_plane_offset) || 0,
          scale: Number(src.metric_scale_factor) || 1, ...atlas, ...(t ? { ...photoreal, trained: true, views: t.views } : {}) };
      });
  }
  if (!cfg.splat) return [];
  const sem = ((await getJSON(cfg.meta)) || {}).semantics_metadata || {};
  const fallback = Object.values(placed).find((p) => p.factory) || {};
  return [{ name: "Racking", label: "Racking aisle", url: cfg.splat, ground: Number(sem.ground_plane_offset) || 0,
    scale: Number(sem.metric_scale_factor) || 1, ...fallback }];
}

async function loadCaptures(cfg, scene, loadingText) {
  const list = await captureList(cfg);
  if (!list.length) return [];
  loadingText.textContent = `Downloading ${list.length > 1 ? `${list.length} captured zones` : "the captured zone"}…`;
  const loaded = await Promise.all(list.map(async (c) => {
    const bytes = await fetchSplat(c.url);
    if (!bytes) return null;
    try {
      let mesh = new SplatMesh({ fileBytes: bytes, fileName: c.url.split("/").pop() });
      await mesh.initialized;
      if (c.crop || c.dilate || c.max_splat_m) mesh = await cropSplats(mesh, c.crop, c.scale, c.ground, c.dilate || 1, c.max_splat_m);
      // Same correction as the USD wrapper: rotateX(180) · translate(0, -ground, 0) · scale(s),
      // then the capture's place in the building.
      const root = new THREE.Group();
      root.position.fromArray(c.position || [0, 0, 0]);
      root.rotation.y = THREE.MathUtils.degToRad(c.yaw_deg || 0);
      const flip = new THREE.Group(); flip.rotation.x = Math.PI;
      const lift = new THREE.Group(); lift.position.y = -c.ground;
      mesh.scale.setScalar(c.scale);
      lift.add(mesh); flip.add(lift); root.add(flip); scene.add(root);
      return c;
    } catch (e) {
      console.warn(`Could not load capture ${c.name}:`, e);
      return null;
    }
  }));
  return loaded.filter(Boolean);
}

// Keeps the splats inside crop.min/max, given in metres in the corrected (Y-up, floor at 0) frame,
// which is how a person reads positions off the twin. Undoes the correction to test raw centres.
// `dilate` grows each splat and firms up its opacity, closing the gaps between depth samples so the
// capture reads as a surface rather than a point cloud. `maxSplat` (metres) drops oversized splats,
// which in a trained capture are mostly haze where the training views were masked (people).
async function cropSplats(src, crop, scale, ground, dilate = 1, maxSplat = Infinity) {
  const lo = new THREE.Vector3(...(crop?.min || [-Infinity, -Infinity, -Infinity]));
  const hi = new THREE.Vector3(...(crop?.max || [Infinity, Infinity, Infinity])), p = new THREE.Vector3();
  const out = new SplatMesh({
    constructSplats: (splats) => {
      src.forEachSplat((_i, center, scales, quat, opacity, color) => {
        p.copy(center).multiplyScalar(scale);
        p.set(p.x, ground - p.y, -p.z); // rotateX(180) after translate(0, -ground, 0)
        if (p.x >= lo.x && p.x <= hi.x && p.y >= lo.y && p.y <= hi.y && p.z >= lo.z && p.z <= hi.z
            && Math.max(scales.x, scales.y, scales.z) * scale <= maxSplat) {
          if (dilate !== 1) { scales.multiplyScalar(dilate); opacity = Math.min(1, opacity * (1 + (dilate - 1) * 2)); }
          splats.pushSplat(center, scales, quat, opacity, color);
        }
      });
    },
  });
  await out.initialized;
  src.dispose?.();
  return out;
}

// ---------- Boot ----------
(async function main() {
  const cfg = { ...DEFAULTS, ...((await getJSON("twin.config.json")) || {}) };
  if (cfg.repo) $("repo-link").href = cfg.repo;
  const apiBase = params.get("api") ?? cfg.telemetryApi;
  if (!recording) startFeed({ apiBase, forceSim: params.has("sim"), onData: render });

  if (hasWebGL2()) {
    try {
      scene3d = await initScene(cfg);
      if (lastData) render(lastData, lastSource);
    } catch (e) {
      console.error(e);
      $("fail").hidden = false;
    }
  } else {
    $("fail").hidden = false;
  }
  $("loading").classList.add("done");
})();
