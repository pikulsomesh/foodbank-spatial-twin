// Operations page: the food bank's whole flow as data in the twin. Reads GET /operations from the
// AWS API when twin.config.json names one, else runs the same model locally (operations.js).
import { startOpsFeed } from "./operations.js";
import { simulate } from "./telemetry.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const NS = "http://www.w3.org/2000/svg";
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const fmt = (n) => Math.round(n).toLocaleString("en-US");
function lbs(n) {
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(2)}M lb`;
  if (Math.abs(n) >= 1e4) return `${Math.round(n / 1e3)}k lb`;
  if (Math.abs(n) >= 1e3) return `${(n / 1e3).toFixed(1)}k lb`;
  return `${Math.round(n)} lb`;
}
const pct = (a, b) => (b > 0 ? Math.min(100, (a / b) * 100) : 0);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

async function getJSON(url) {
  try { const r = await fetch(url, { cache: "no-cache" }); return r.ok ? await r.json() : null; } catch { return null; }
}

// ---------- Tooltip ----------
const tip = $("tip");
function showTip(e, html) {
  tip.innerHTML = html;
  tip.hidden = false;
  const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
  let x = e.clientX + pad, y = e.clientY + pad;
  if (x + w > innerWidth - 8) x = e.clientX - w - pad;
  if (y + h > innerHeight - 8) y = e.clientY - h - pad;
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${Math.max(8, y)}px`;
}
document.addEventListener("pointermove", (e) => {
  const t = e.target.closest?.("[data-tip]");
  if (t) showTip(e, t.dataset.tip); else tip.hidden = true;
});
document.addEventListener("scroll", () => { tip.hidden = true; }, { passive: true });

function el(tag, attrs = {}, parent) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) n.setAttribute(k, v);
  parent?.append(n);
  return n;
}
function text(parent, x, y, str, attrs = {}) {
  const t = el("text", { x, y, ...attrs }, parent);
  t.textContent = str;
  return t;
}

// ---------- KPIs ----------
function renderKpis(d) {
  const k = d.kpis, kit = d.kitchen;
  const risk = d.areas.filter((a) => a.cold_status && a.cold_status !== "ok");
  const tiles = [
    { label: "Received today", value: lbs(k.received_lbs), meta: `of ${lbs(k.received_plan_lbs)} planned`, bar: pct(k.received_lbs, k.received_plan_lbs) },
    { label: "Distributed today", value: lbs(k.distributed_lbs), meta: `≈ ${fmt(k.meals_equivalent)} meals at 1.2 lb a meal`, bar: pct(k.distributed_lbs, k.distributed_plan_lbs) },
    { label: "On hand", value: lbs(k.on_hand_lbs), meta: `${k.days_of_supply} days of supply` },
    { label: "Kitchen meals out", value: `${fmt(k.meals_dispatched)}`, meta: kit.open ? `of ${fmt(k.meals_planned)} planned` : "Kitchen closed today", bar: pct(k.meals_dispatched, k.meals_planned) },
    { label: "Trucks unloaded", value: `${k.trucks_received} of ${k.trucks_planned}`, meta: `${k.pickups_done} of ${k.pickups_planned} partner pickups done`, bar: pct(k.trucks_received, k.trucks_planned) },
    { label: "Cold chain", value: k.at_risk_lbs ? lbs(k.at_risk_lbs) : "In range",
      meta: risk.length ? risk.map((a) => `${a.label} ${a.cold_status === "alarm" ? "alarm" : "drifting"}`).join(", ") : "No stock at risk",
      status: k.at_risk_lbs ? "alarm" : risk.length ? "warn" : "ok" },
  ];
  $("kpis").innerHTML = tiles.map((t) => `<div class="kpi"><div class="label">${t.label}</div>
    <div class="value">${t.status ? `<span class="status ${t.status}"><span>${esc(t.value)}</span></span>` : esc(t.value)}</div>
    <div class="meta">${esc(t.meta)}</div>${t.bar !== undefined ? `<div class="bar"><i style="width:${t.bar}%"></i></div>` : ""}</div>`).join("");
}

// ---------- Sankey ----------
let DATA = null;
const COLUMNS = () => [
  DATA.sources.map((s) => s.id), ["receiving"], ["sorting"], ["freezer", "cooler", "dry"], ["kitchen", "staging", "market"],
  DATA.channels.map((c) => c.id),
];
const SHORT = { receiving: "Receiving dock", sorting: "Sorting", freezer: "Freezer", cooler: "Cooler", dry: "Dry racking",
  kitchen: "Community kitchen", staging: "Order staging", market: "Client market", partners: "Partner agencies",
  mobile: "Mobile pantries", clients: "Market shoppers", meals: "Kids and senior meals", usda: "USDA commodities",
  retail: "Grocery rescue", growers: "Produce growers", purchased: "Purchased", drives: "Food drives" };
const TEMP = { freezer: "--s1", cooler: "--s3", dry: "--s2" };
function bandColor(f) {
  if (f.from === "kitchen") return css("--s5");
  if (TEMP[f.to] && f.from !== "receiving" && f.from !== "sorting") return css(TEMP[f.to]);
  if (TEMP[f.from]) return css(TEMP[f.from]);
  if (TEMP[f.to]) return css(TEMP[f.to]);
  return "#6b7682";
}

function renderSankey(d) {
  const svg = $("sankey");
  svg.replaceChildren();
  const W = 1120, H = 430, left = 132, right = 150, top = 8, nodeW = 10, gap = 16;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.style.minWidth = "760px";
  svg.style.width = "100%";
  const flows = d.flows.filter((f) => f.lbs_plan > 0);
  if (!flows.length) {
    svg.setAttribute("viewBox", `0 0 ${W} 60`);
    text(svg, W / 2, 34, "The warehouse is closed today, so nothing moves. Stock carries over to tomorrow.", { "text-anchor": "middle" });
    return;
  }
  const inSum = {}, outSum = {};
  for (const f of flows) { outSum[f.from] = (outSum[f.from] || 0) + f.lbs_plan; inSum[f.to] = (inSum[f.to] || 0) + f.lbs_plan; }
  const cols = COLUMNS();
  const value = (id) => Math.max(inSum[id] || 0, outSum[id] || 0);
  const k = Math.min(...cols.map((c) => (H - top * 2 - (gap + 28) * (c.length - 1)) / c.reduce((a, id) => a + value(id), 0)));
  const nodes = {};
  cols.forEach((c, ci) => {
    const x = left + (ci * (W - left - right - nodeW)) / (cols.length - 1);
    // Small nodes still get a label's worth of room, so their two-line labels don't collide.
    const slot = (id) => Math.max(28, Math.max(2, value(id) * k));
    const total = c.reduce((a, id) => a + slot(id), 0) + gap * (c.length - 1);
    let y = top + Math.max(0, (H - top * 2 - total) / 2);
    for (const id of c) {
      const h = Math.max(2, value(id) * k);
      nodes[id] = { id, x, y, h, col: ci, outY: y, inY: y };
      y += slot(id) + gap;
    }
  });
  const bands = el("g", {}, svg);
  const ordered = [...flows].sort((a, b) => nodes[a.from].y - nodes[b.from].y || nodes[a.to].y - nodes[b.to].y);
  const byTarget = [...flows].sort((a, b) => nodes[a.to].y - nodes[b.to].y || nodes[a.from].y - nodes[b.from].y);
  const off = new Map();
  for (const f of ordered) { const n = nodes[f.from], w = f.lbs_plan * k; off.set(f, { y0: n.outY, w }); n.outY += w; }
  for (const f of byTarget) { const n = nodes[f.to]; off.get(f).y1 = n.inY; n.inY += f.lbs_plan * k; }
  for (const f of ordered) {
    const a = nodes[f.from], b = nodes[f.to], { y0, y1, w } = off.get(f);
    const x0 = a.x + nodeW, x1 = b.x, mx = (x0 + x1) / 2;
    const path = `M${x0},${y0} C${mx},${y0} ${mx},${y1} ${x1},${y1} L${x1},${y1 + w} C${mx},${y1 + w} ${mx},${y0 + w} ${x0},${y0 + w} Z`;
    el("path", { d: path, fill: bandColor(f), opacity: 0.42, class: "link-band",
      "data-tip": `<b>${esc(SHORT[f.from] || f.from)} → ${esc(SHORT[f.to] || f.to)}</b><br>${fmt(f.lbs_today)} of ${fmt(f.lbs_plan)} lb moved today (${Math.round(pct(f.lbs_today, f.lbs_plan))}%)` }, bands);
  }
  const g = el("g", {}, svg);
  for (const n of Object.values(nodes)) {
    const moved = d.flows.filter((f) => f.to === n.id).reduce((a, f) => a + f.lbs_today, 0)
      || d.flows.filter((f) => f.from === n.id).reduce((a, f) => a + f.lbs_today, 0);
    const tipHtml = `<b>${esc(SHORT[n.id] || n.id)}</b><br>${fmt(moved)} of ${fmt(value(n.id))} lb so far today`;
    el("rect", { x: n.x, y: n.y, width: nodeW, height: n.h, rx: 2, fill: "#c3cdd6", "data-tip": tipHtml, class: "hot" }, g);
    const first = n.col === 0, last = n.col === cols.length - 1;
    const tx = first ? n.x - 8 : n.x + nodeW + 8, anchor = first ? "end" : "start";
    const ty = n.y + n.h / 2;
    const sub = n.id === "kitchen" ? `${lbs(value(n.id))} → ${fmt(d.kitchen.meals_planned)} meals` : lbs(value(n.id));
    const halo = { "paint-order": "stroke", stroke: css("--card"), "stroke-width": 4, "stroke-linejoin": "round" };
    text(g, tx, ty - 2, SHORT[n.id] || n.id, { "text-anchor": anchor, class: "strong", ...(last || first ? {} : halo) });
    text(g, tx, ty + 12, sub, { "text-anchor": anchor, class: "muted", ...(last || first ? {} : halo) });
  }
  $("flow-legend").innerHTML = [["--s1", "Frozen"], ["--s3", "Chilled"], ["--s2", "Ambient (dry)"], [null, "Mixed loads before storage and after staging"], ["--s5", "Kitchen meals"]]
    .map(([v, l]) => `<span><i style="background:${v ? css(v) : "#6b7682"}"></i>${l}</span>`).join("");
}

// ---------- Site plan ----------
const PLAN = { x0: -4, x1: 39, z0: -1, z1: 21, s: 19 };
const RAMP = ["--q1", "--q2", "--q3", "--q4", "--q5", "--q6", "--q7"];
const rampIndex = (u) => Math.min(RAMP.length - 1, Math.floor(Math.max(0, u) * RAMP.length));
const rampColor = (u) => css(RAMP[rampIndex(u)]);
function camLink(a) {
  const [cx, , cz] = a.position;
  return `./?cam=${[cx - 3, 9, cz + 13, cx, 0.5, cz].map((v) => +v.toFixed(2)).join(",")}`;
}
function renderPlan(d, sensors) {
  const svg = $("plan");
  svg.replaceChildren();
  const { x0, x1, z0, z1, s } = PLAN, X = (x) => (x - x0) * s, Z = (z) => (z - z0) * s;
  svg.setAttribute("viewBox", `0 0 ${(x1 - x0) * s} ${(z1 - z0) * s}`);
  svg.style.minWidth = "620px";
  svg.style.width = "100%";
  // Building shell and the kitchen annex.
  el("rect", { x: X(0), y: Z(0), width: 26 * s, height: 20 * s, fill: "none", stroke: "#5d6670", "stroke-width": 1.5, rx: 2 }, svg);
  const kitchenArea = d.areas.find((a) => a.id === "kitchen");
  if (kitchenArea) {
    const f = kitchenArea.footprint;
    el("rect", { x: X(f.x[0]), y: Z(f.z[0]), width: (f.x[1] - f.x[0]) * s, height: (f.z[1] - f.z[0]) * s, fill: "none", stroke: "#5d6670", "stroke-width": 1.5, "stroke-dasharray": "6 4", rx: 2 }, svg);
  }
  // Dock doors on the x = 0 wall (the twin has three; the real building has 15).
  for (const z of [8.6, 11.6, 14.6]) el("rect", { x: X(-0.15), y: Z(z), width: 0.3 * s, height: 2.4 * s, fill: css("--card") }, svg);
  text(svg, X(-0.6), Z(7.6), "Docks", { "text-anchor": "end", class: "muted" });

  for (const a of d.areas) {
    const f = a.footprint, w = (f.x[1] - f.x[0]) * s, h = (f.z[1] - f.z[0]) * s;
    const link = el("a", { href: camLink(a), class: "area", "aria-label": `${a.label}, ${Math.round(a.utilisation * 100)}% full. View in 3D.` }, svg);
    const tipHtml = `<b>${esc(a.label)}</b><br>${fmt(a.on_hand_lbs)} lb on ${fmt(a.pallets)} of ${fmt(a.capacity_pallets)} pallet positions (${Math.round(a.utilisation * 100)}%)` +
      `${a.cold_status ? `<br>Cold chain: ${a.cold_status}` : ""}${a.capture ? `<br>Captured from video: ${esc(a.capture)}` : "<br>Not captured yet"}<br>Select to view in 3D`;
    el("rect", { x: X(f.x[0]) + 2, y: Z(f.z[0]) + 2, width: w - 4, height: h - 4, rx: 4, fill: rampColor(a.utilisation), "fill-opacity": 0.85, class: "fill", "data-tip": tipHtml }, link);
    // Light ramp steps take dark ink so labels keep their contrast.
    const ink = rampIndex(a.utilisation) >= 4 ? "#0b0b0b" : css("--text");
    const lx = X(f.x[0]) + 8, ly = Z(f.z[0]) + 18;
    text(link, lx, ly, a.label, { class: "strong", style: `fill:${ink}`, "pointer-events": "none" });
    text(link, lx, ly + 15, `${Math.round(a.utilisation * 100)}% · ${fmt(a.pallets)} pallets`, { style: `fill:${ink}`, "pointer-events": "none" });
    if (a.capture) {
      const cx = X(f.x[1]) - 22, cy = Z(f.z[1]) - 19;
      el("rect", { x: cx, y: cy, width: 14, height: 10, rx: 2, fill: "none", stroke: ink, "stroke-width": 1.2, "pointer-events": "none" }, link);
      el("circle", { cx: cx + 7, cy: cy + 5, r: 2.6, fill: "none", stroke: ink, "stroke-width": 1.2, "pointer-events": "none" }, link);
    }
  }
  // Kitchen stations: filled while a batch is using them.
  for (const e of d.kitchen.equipment) {
    const [x, , z] = e.position;
    el("circle", { cx: X(x), cy: Z(z), r: 5, fill: e.in_use ? css("--s5") : css("--card"), stroke: css("--text-2"), "stroke-width": 1.2,
      "data-tip": `<b>${esc(e.label)}</b><br>${e.in_use} of ${e.count} in use`, class: "hot" }, svg);
  }
  // Cold-room sensors and the forklift, from the telemetry model.
  const status = { ok: css("--good"), warn: css("--warning"), alarm: css("--critical") };
  for (const z of sensors.zones) {
    el("circle", { cx: X(z.position[0]), cy: Z(z.position[2]), r: 6, fill: status[z.status], stroke: css("--card"), "stroke-width": 2,
      "data-tip": `<b>${esc(z.label)}</b><br>${z.temperature_c.toFixed(1)}°C, set ${z.setpoint_c.toFixed(1)}°C (${z.status})`, class: "hot" }, svg);
  }
  for (const f of sensors.forklifts) {
    el("rect", { x: X(f.position[0]) - 5, y: Z(f.position[2]) - 5, width: 10, height: 10, rx: 2, fill: css("--warning"), stroke: css("--card"), "stroke-width": 2,
      "data-tip": `<b>${esc(f.label)}</b><br>Battery ${f.battery_pct.toFixed(0)}%`, class: "hot" }, svg);
  }
  // Trucks at the doors.
  const atDock = d.trucks.inbound.filter((t) => t.status === "at dock");
  atDock.slice(0, 3).forEach((t, i) => {
    const z = [8.6, 11.6, 14.6][i];
    el("rect", { x: X(-3.4), y: Z(z) + 4, width: 3 * s, height: 2.4 * s - 8, rx: 3, fill: "#6b7682",
      "data-tip": `<b>${esc(t.id)}</b> at door ${t.door}<br>${esc(t.label)}, ${fmt(t.lbs)} lb`, class: "hot" }, svg);
  });
  $("plan-ramp").innerHTML = `<span>0%</span><span class="steps">${RAMP.map((v) => `<i style="background:${css(v)}"></i>`).join("")}</span><span>100% of pallet positions</span>
    <span style="margin-left:auto">● sensor &nbsp;■ forklift &nbsp;○ kitchen station</span>`;
}

// ---------- Kitchen ----------
const STAGES = [["prep", "Prep", "--s1"], ["cook", "Cook", "--s2"], ["chill", "Blast chill", "--s3"], ["pack", "Portion and pack", "--s4"], ["hold", "Cold hold", "--s7"]];
const parseClock = (s) => { const [h, m] = s.split(":").map(Number); return h + m / 60; };
function renderKitchen(d) {
  const kit = d.kitchen, K = DATA.kitchen;
  $("kitchen-sub").textContent = kit.open
    ? `Today: ${kit.recipe.label}. ${kit.programs.map((p) => `${p.label} ${fmt(p.meals)}`).join(" · ")}.`
    : "The kitchen cooks Monday to Friday; it is closed today.";
  $("kitchen-stats").innerHTML = [["Planned", kit.meals_planned], ["Cooked", kit.meals_cooked], ["Packed", kit.meals_packed], ["Sent out", kit.meals_dispatched]]
    .map(([l, v]) => `<div><b>${fmt(v)}</b><span>${l}</span></div>`).join("");
  $("gantt-legend").innerHTML = STAGES.map(([, l, v]) => `<span><i style="background:${css(v)}"></i>${l}</span>`).join("");
  const svg = $("gantt");
  svg.replaceChildren();
  const h0 = 5, h1 = 16, W = 560, left = 92, rowH = 22, top = 22;
  const rows = kit.batches.length + 1;
  const H = top + rows * rowH + 6;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.style.minWidth = "460px";
  svg.style.width = "100%";
  const X = (h) => left + ((h - h0) / (h1 - h0)) * (W - left - 8);
  const now = parseClock(d.local_time);
  for (let h = h0; h <= h1; h += 1) {
    el("line", { x1: X(h), x2: X(h), y1: top - 4, y2: H - 4, stroke: css("--grid"), "stroke-width": 1 }, svg);
    if ((h - h0) % 2 === 0 && !(kit.open && now > h - 0.6 && now < h + 1.6)) text(svg, X(h), 12, `${String(h).padStart(2, "0")}:00`, { "text-anchor": "middle", class: "muted" });
  }
  if (!kit.open) return;
  const [pa, pb] = DATA.flow.kitchen_pick_window;
  text(svg, left - 8, top + 14, "Pick stock", { "text-anchor": "end" });
  el("rect", { x: X(pa), y: top + 4, width: X(pb) - X(pa), height: rowH - 8, rx: 3, fill: "#6b7682",
    "data-tip": "<b>Ingredients picked from storage</b><br>Frozen, cooler and dry stock for today's recipe", class: "hot" }, svg);
  const [da, db] = K.dispatch_window;
  kit.batches.forEach((b, i) => {
    const y = top + (i + 1) * rowH;
    text(svg, left - 8, y + 14, `${b.id} · ${b.meals}`, { "text-anchor": "end" });
    let t = parseClock(b.start);
    for (const step of K.routing) {
      const end = step.hours === null ? da + ((i + 1) / kit.batches.length) * (db - da) : t + step.hours;
      const [, label, v] = STAGES.find(([s]) => s === step.step);
      const eq = step.equipment || K.recipes.find((r) => r.id === kit.recipe.id).cook;
      const eqLabel = K.equipment.find((e) => e.id === eq)?.label || eq;
      el("rect", { x: X(t) + 1, y: y + 4, width: Math.max(1, X(end) - X(t) - 2), height: rowH - 8, rx: 3, fill: css(v),
        opacity: b.stage === step.step ? 1 : 0.7,
        "data-tip": `<b>${b.id}: ${label}</b><br>${eqLabel}<br>${fmt(b.meals)} meals${b.stage === step.step ? "<br>In progress now" : ""}`, class: "hot" }, svg);
      t = end;
    }
  });
  if (now >= h0 && now <= h1) {
    el("line", { x1: X(now), x2: X(now), y1: top - 4, y2: H - 2, stroke: css("--text"), "stroke-width": 2 }, svg);
    text(svg, X(now) + 4, top - 8, `now ${d.local_time}`, { class: "strong" });
  }
  $("kitchen-eq").innerHTML = kit.equipment.map((e) => `<span class="chip${e.in_use ? " busy" : ""}">${esc(e.label)} ${e.in_use}/${e.count}</span>`).join("")
    + (kit.cold_hold ? `<span class="chip">Walk-in <span class="status ${kit.cold_hold.status}"><span>${kit.cold_hold.temperature_c.toFixed(1)}°C</span></span></span>` : "");
}

// ---------- Inventory and docks ----------
function renderInventory(d) {
  const label = Object.fromEntries(d.areas.map((a) => [a.id, a.label]));
  const color = Object.fromEntries(DATA.categories.map((c) => [c.id, css(`--s${c.color}`)]));
  $("inventory").tBodies[0].innerHTML = d.inventory.map((i) => {
    const max = i.target_days * 2;
    return `<tr><td><span class="swatch" style="background:${color[i.id]}"></span>${esc(i.label)}</td><td>${esc(label[i.zone])}</td>
      <td class="n">${fmt(i.on_hand_lbs)} lb</td><td class="n">${fmt(i.pallets)}</td>
      <td><div class="dos" data-tip="${esc(`<b>${i.label}</b><br>${i.days_of_supply} days on hand, target ${i.target_days}`)}"><div class="bar"><i style="width:${pct(i.days_of_supply, max)}%"></i><b style="left:50%"></b></div><span>${i.days_of_supply} d</span></div></td>
      <td class="n">${fmt(i.received_today_lbs)}</td><td class="n">${fmt(i.shipped_today_lbs)}</td><td class="n">${fmt(i.shelf_life_days)} d</td>
      <td><span class="status ${i.status}"><span>${{ ok: "On target", low: "Running low", high: "Overstocked" }[i.status]}</span></span></td></tr>`;
  }).join("");
}
function renderDocks(d) {
  const t = d.trucks;
  $("in-sub").textContent = t.inbound.length ? `${d.kpis.trucks_received} of ${t.inbound.length} unloaded` : "No deliveries today";
  $("inbound").tBodies[0].innerHTML = t.inbound.map((x) => `<tr><td>${x.id}</td><td>${esc(x.label)}</td><td class="n">${fmt(x.lbs)}</td><td class="n">${x.door}</td><td class="n">${x.eta}</td>
    <td><span class="status ${x.status.replace(" ", "-")}"><span>${x.status}</span></span></td></tr>`).join("");
  $("out-sub").textContent = t.outbound_planned ? `${t.outbound_done} of ${t.outbound_planned} done` : "No pickups today";
  $("outbound").tBodies[0].innerHTML = t.outbound_next.length ? t.outbound_next.map((x) => `<tr><td>${x.id}</td><td>${esc(x.label)}</td><td class="n">${fmt(x.lbs)}</td><td class="n">${x.door}</td><td class="n">${x.eta}</td>
    <td><span class="status ${x.status}"><span>${x.status}</span></span></td></tr>`).join("")
    : `<tr><td colspan="6" style="color:var(--muted)">All of today's pickups are done.</td></tr>`;
}

function setFeed(source) {
  const pill = $("feed-pill");
  pill.classList.toggle("live", source === "live");
  pill.classList.toggle("sim", source !== "live");
  pill.querySelector("span").textContent = source === "live" ? "Live from AWS (simulated operations)" : "Simulated (offline)";
}

let lastKey = "";
function render(d, source) {
  const sensors = simulate();
  setFeed(source);
  $("clock").textContent = `${d.local_time} local`;
  renderKpis(d);
  renderPlan(d, sensors);
  renderInventory(d);
  renderDocks(d);
  // The flow and kitchen charts only change shape once a minute; skip redrawing them in between.
  const key = `${d.local_time}|${source}`;
  if (key !== lastKey) { renderSankey(d); renderKitchen(d); lastKey = key; }
}

(async function main() {
  const [cfg, data] = await Promise.all([getJSON("twin.config.json"), getJSON("ops/operations.json")]);
  if (!data) { document.querySelector("main").insertAdjacentHTML("afterbegin", "<p>Could not load the operations data.</p>"); return; }
  DATA = data;
  $("refs").innerHTML = data.references.map((r) => `<li><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a></li>`).join("");
  const apiBase = params.get("api") ?? cfg?.telemetryApi ?? "";
  startOpsFeed({ apiBase, data, forceSim: params.has("sim"), coldZones: () => simulate().zones, onData: render });
})();
