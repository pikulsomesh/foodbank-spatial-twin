// Whole-site operations for the food bank twin: inventory, flows, docks and the community kitchen.
// A line-for-line port of src/telemetry/operations.py (GET /operations), fed the same master data
// (ops/operations.json, a copy of src/telemetry/operations.json), so the page shows the same numbers
// whether it reads the AWS API or simulates locally. tests/operations.test.mjs compares the two.

function hash01(key) {
  let h = 2166136261;
  for (const ch of key) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  h = Math.imul(h ^ (h >>> 15), 2246822507);
  h = Math.imul(h ^ (h >>> 13), 3266489909);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
}
const noise = (key) => hash01(key) * 2 - 1;
const r = (v, d = 0) => { const k = 10 ** d; return Math.floor(v * k + 0.5) / k; };
const clamp01 = (v) => Math.min(1, Math.max(0, v));
const sum = (xs) => xs.reduce((a, b) => a + b, 0);
const cum = (windows, h) => sum(windows.map(([a, b, s]) => s * clamp01((h - a) / (b - a))));
function inv(windows, p) {
  let acc = 0;
  for (const [a, b, s] of windows) {
    if (p <= acc + s) return a + ((p - acc) / s) * (b - a);
    acc += s;
  }
  return windows.at(-1)[1];
}
const pad = (n) => String(n).padStart(2, "0");
function clock(h) {
  h = ((h % 24) + 24) % 24;
  const hh = Math.floor(h);
  return `${pad(hh)}:${pad(Math.floor((h - hh) * 60))}`;
}
const mod = (a, n) => ((a % n) + n) % n;
const weekday = (day) => mod(day - 4, 7); // the Unix epoch was a Thursday; 0 = Monday
const opDays = (day) => { const d = day - 4; return 6 * Math.floor(d / 7) + Math.min(mod(d, 7), 6); };

// Precomputes the derived constants once per master-data object.
function model(data) {
  if (data.__model) return data.__model;
  const SITE = data.site, CATS = data.categories, CAT_IDS = CATS.map((c) => c.id);
  const CAT_SHARE = Object.fromEntries(CAT_IDS.map((c) => [c, sum(data.sources.map((s) => s.share * (s.mix[c] || 0)))]));
  const SORT_SHARE = Object.fromEntries(CAT_IDS.map((c) => [c,
    sum(data.sources.filter((s) => s.sorted).map((s) => s.share * (s.mix[c] || 0))) / CAT_SHARE[c]]));
  const m = {
    SITE, FLOW: data.flow, KITCHEN: data.kitchen, CATS, CAT_IDS, CAT_SHARE, SORT_SHARE,
    AREAS: Object.fromEntries(data.zones.map((z) => [z.id, z])),
    BASE_DAY_LBS: SITE.annual_lbs / (52 * sum(SITE.operating_weekdays)),
    MEAN_DAY_LBS: SITE.annual_lbs / 365,
    CHANNELS: data.channels.filter((c) => !c.from_kitchen),
  };
  Object.defineProperty(data, "__model", { value: m });
  return m;
}

export function dayPlan(data, day) {
  const M = model(data), K = M.KITCHEN;
  const w = weekday(day);
  const meals = r(K.meals_per_day * K.operating_weekdays[w] * (1 + 0.05 * noise(`meals:${day}`)));
  const recipe = K.recipes[mod(opDays(day), K.recipes.length)];
  const inbound = M.BASE_DAY_LBS * M.SITE.operating_weekdays[w] * (1 + 0.08 * noise(`in:${day}`));
  const target = (i, c, d) => c.days_of_supply * M.MEAN_DAY_LBS * M.CAT_SHARE[c.id] * (1 + 0.03 * Math.sin((2 * Math.PI * opDays(d)) / 29 + i * 1.3));
  const cats = {};
  M.CATS.forEach((c, i) => {
    const s0 = target(i, c, day), s1 = target(i, c, day + 1);
    const cin = inbound * M.CAT_SHARE[c.id];
    const out = cin + s0 - s1;
    const kit = meals * (recipe.bom_lbs[c.id] || 0);
    cats[c.id] = { open: s0, in: cin, out, kitchen: kit, rest: out - kit };
  });
  return { inbound, meals, recipe, cats };
}

function kitchenState(M, day, h, meals, recipe, cold) {
  const K = M.KITCHEN, size = K.batch_meals;
  const n = meals ? Math.ceil(meals / size) : 0;
  const [da, db] = K.dispatch_window;
  const batches = [], done = { cook: 0, chill: 0, pack: 0 };
  const inUse = Object.fromEntries(K.equipment.map((e) => [e.id, 0]));
  for (let i = 0; i < n; i++) {
    const m = Math.min(size, meals - i * size);
    let t = K.first_batch_h + i * K.batch_stagger_h, stage = "queued", equipment = null;
    for (const step of K.routing) {
      const start = t, end = step.hours === null ? da + ((i + 1) / n) * (db - da) : t + step.hours;
      const eq = step.equipment || recipe.cook;
      if (start <= h && h < end) { stage = step.step; equipment = eq; inUse[eq] = (inUse[eq] || 0) + 1; }
      if (h >= end && step.step in done) done[step.step] += m;
      t = end;
    }
    if (h >= t) stage = "dispatched";
    batches.push({ id: `B${i + 1}`, meals: m, stage, equipment, start: clock(K.first_batch_h + i * K.batch_stagger_h), dispatch: clock(t) });
  }
  const dispatched = sum(batches.filter((b) => b.stage === "dispatched").map((b) => b.meals));
  const hold = cold[M.AREAS.kitchen.telemetry_zone];
  return {
    label: K.label, open: meals > 0,
    recipe: meals ? { id: recipe.id, label: recipe.label } : null,
    meals_planned: meals, meals_cooked: done.cook, meals_chilled: done.chill, meals_packed: done.pack, meals_dispatched: dispatched,
    programs: K.programs.map((p) => ({ id: p.id, label: p.label, meals: r(meals * p.share) })),
    batches,
    equipment: K.equipment.map((e) => ({ id: e.id, label: e.label, count: e.count, in_use: inUse[e.id] || 0, position: e.position })),
    cold_hold: hold ? { temperature_c: hold.temperature_c, status: hold.status } : null,
  };
}

function trucks(M, data, day, h, plan) {
  const F = M.FLOW, inbound = [];
  if (plan.inbound > 0) {
    const loads = [];
    for (const s of data.sources) {
      const lbs = plan.inbound * s.share;
      const n = Math.max(1, r(lbs / F.lbs_per_inbound_truck));
      for (let j = 0; j < n; j++) loads.push([hash01(`truck:${day}:${s.id}:${j}`), s, lbs / n]);
    }
    loads.sort((a, b) => a[0] - b[0]);
    loads.forEach(([, s, lbs], k) => {
      const eta = inv(F.inbound_windows, (k + 0.5) / loads.length) + 0.2 * noise(`eta:${day}:${k}`);
      const status = h < eta ? "scheduled" : h < eta + F.putaway_lag_h ? "at dock" : "received";
      inbound.push({ id: `IN-${pad(k + 1)}`, source: s.id, label: s.label, lbs: r(lbs), door: (k % F.docks) + 1, eta: clock(eta), status });
    });
  }
  const rest = sum(Object.values(plan.cats).map((c) => c.rest)) * sum(M.CHANNELS.filter((c) => c.zone === "staging").map((c) => c.share));
  const nOut = rest > 0 ? r(rest / F.lbs_per_partner_pickup) : 0;
  const kinds = ["Pantry", "Shelter", "School pantry", "Senior center", "Mobile pantry"];
  const outbound = [];
  let done = 0;
  for (let j = 0; j < nOut; j++) {
    const eta = inv(F.outbound_windows, (j + 0.5) / nOut);
    if (h >= eta + 0.25) { done++; continue; }
    if (outbound.length < 8) {
      const agency = 1 + (Math.floor(hash01(`agency:${day}:${j}`) * M.SITE.partner_agencies) % M.SITE.partner_agencies);
      outbound.push({ id: `OUT-${pad(j + 1)}`, label: `${kinds[agency % kinds.length]} ${String(agency).padStart(3, "0")}`,
        lbs: r(rest / nOut), door: F.docks - (j % 5), eta: clock(eta), status: h >= eta ? "loading" : "scheduled" });
    }
  }
  return { inbound, outbound_next: outbound, outbound_done: done, outbound_planned: nOut };
}

// Operations payload at `nowMs`; `coldZones` is /telemetry's zones[] (for cold-chain risk).
export function simulateOps(data, nowMs = Date.now(), coldZones = []) {
  const M = model(data), F = M.FLOW;
  const now = nowMs / 1000;
  const cold = Object.fromEntries(coldZones.map((z) => [z.id, z]));
  const local = now + M.SITE.utc_offset_hours * 3600;
  const day = Math.floor(local / 86400), h = mod(local, 86400) / 3600;
  const plan = dayPlan(data, day);
  const R = cum(F.inbound_windows, h), Pd = cum(F.inbound_windows, h - F.putaway_lag_h);
  const T = cum(F.inbound_windows, h - F.to_sorting_lag_h), O = cum(F.inbound_windows, h - F.sorted_lag_h);
  const Co = cum(F.outbound_windows, h), Cp = cum(F.outbound_windows, h + 1);
  const [ka, kb] = F.kitchen_pick_window;
  const Ck = clamp01((h - ka) / (kb - ka));
  const kitchen = kitchenState(M, day, h, plan.meals, plan.recipe, cold);
  const disp = plan.meals ? kitchen.meals_dispatched / plan.meals : 0;
  const stagingShare = sum(M.CHANNELS.filter((c) => c.zone === "staging").map((c) => c.share));
  const marketShare = sum(M.CHANNELS.filter((c) => c.zone === "market").map((c) => c.share));

  const areaLbs = Object.fromEntries(Object.keys(M.AREAS).map((a) => [a, Object.fromEntries(M.CAT_IDS.map((c) => [c, 0]))]));
  const inventory = [], edges = new Map();
  const edge = (a, b, nowLbs, planLbs) => {
    const k = `${a}\u0000${b}`;
    const e = edges.get(k) || [a, b, 0, 0];
    e[2] += nowLbs; e[3] += planLbs;
    edges.set(k, e);
  };
  for (const s of data.sources) edge(s.id, "receiving", plan.inbound * s.share * R, plan.inbound * s.share);
  for (const c of M.CATS) {
    const p = plan.cats[c.id], f = M.SORT_SHARE[c.id], store = c.zone;
    areaLbs.receiving[c.id] += p.in * ((1 - f) * (R - Pd) + f * (R - T));
    areaLbs.sorting[c.id] += p.in * f * (T - O);
    const onHand = p.open + p.in * ((1 - f) * Pd + f * O) - p.kitchen * Ck - p.rest * Cp;
    areaLbs[store][c.id] += onHand;
    areaLbs.staging[c.id] += p.rest * stagingShare * (Cp - Co);
    areaLbs.market[c.id] += p.rest * marketShare * (Cp - Co);
    areaLbs.kitchen[c.id] += p.kitchen * Math.max(0, Ck - disp);
    edge("receiving", "sorting", p.in * f * T, p.in * f);
    edge("receiving", store, p.in * (1 - f) * Pd, p.in * (1 - f));
    edge("sorting", store, p.in * f * O, p.in * f);
    edge(store, "kitchen", p.kitchen * Ck, p.kitchen);
    edge(store, "staging", p.rest * stagingShare * Cp, p.rest * stagingShare);
    edge(store, "market", p.rest * marketShare * Cp, p.rest * marketShare);
    for (const ch of M.CHANNELS) edge(ch.zone, ch.id, p.rest * ch.share * Co, p.rest * ch.share);
    edge("kitchen", "meals", p.kitchen * disp, p.kitchen);
    const dos = onHand / (M.MEAN_DAY_LBS * M.CAT_SHARE[c.id]);
    inventory.push({
      id: c.id, label: c.label, zone: store, on_hand_lbs: r(onHand), pallets: r(onHand / c.lbs_per_pallet),
      days_of_supply: r(dos, 1), target_days: c.days_of_supply, shelf_life_days: c.shelf_life_days,
      received_today_lbs: r(p.in * R), shipped_today_lbs: r(p.rest * Co + p.kitchen * Ck),
      status: dos < 0.6 * c.days_of_supply ? "low" : dos > 1.4 * c.days_of_supply ? "high" : "ok",
    });
  }

  const palletLbs = Object.fromEntries(M.CATS.map((c) => [c.id, c.lbs_per_pallet]));
  const areas = data.zones.map((a) => {
    const lbs = sum(Object.values(areaLbs[a.id]));
    const pallets = sum(Object.entries(areaLbs[a.id]).map(([c, v]) => v / palletLbs[c]));
    const tz = a.telemetry_zone ? cold[a.telemetry_zone] : null;
    const { x, z } = a.footprint;
    return {
      id: a.id, label: a.label, kind: a.kind, temp: a.temp, footprint: a.footprint,
      position: [(x[0] + x[1]) / 2, 0, (z[0] + z[1]) / 2], capture: a.capture,
      on_hand_lbs: r(lbs), pallets: r(pallets), capacity_pallets: a.capacity_pallets,
      utilisation: r(pallets / a.capacity_pallets, 3), telemetry_zone: a.telemetry_zone,
      cold_status: tz ? tz.status : null, at_risk_lbs: tz && tz.status === "alarm" ? r(lbs) : 0,
    };
  });

  const flows = [...edges.values()].map(([from, to, a, b]) => ({ from, to, lbs_today: r(a), lbs_plan: r(b) }));
  const tr = trucks(M, data, day, h, plan);
  const channelIds = new Set(data.channels.map((c) => c.id));
  const onHand = sum(inventory.map((i) => i.on_hand_lbs));
  const distributed = sum(flows.filter((f) => channelIds.has(f.to)).map((f) => f.lbs_today));
  const distributedPlan = sum(flows.filter((f) => channelIds.has(f.to)).map((f) => f.lbs_plan));
  return {
    site: M.SITE.name, simulated: true,
    timestamp: new Date(Math.floor(now) * 1000).toISOString().replace(".000", ""),
    local_time: clock(h), open: plan.inbound > 0,
    units: { mass: "lb", position: "m" },
    kpis: {
      received_lbs: r(plan.inbound * R), received_plan_lbs: r(plan.inbound),
      distributed_lbs: distributed, distributed_plan_lbs: distributedPlan,
      meals_equivalent: r(distributed / M.SITE.lbs_per_meal),
      on_hand_lbs: onHand, days_of_supply: r(onHand / M.MEAN_DAY_LBS, 1),
      meals_planned: kitchen.meals_planned, meals_dispatched: kitchen.meals_dispatched,
      trucks_received: tr.inbound.filter((t) => t.status === "received").length, trucks_planned: tr.inbound.length,
      pickups_done: tr.outbound_done, pickups_planned: tr.outbound_planned,
      at_risk_lbs: sum(areas.map((a) => a.at_risk_lbs)),
    },
    areas, inventory, flows, kitchen, trucks: tr,
  };
}

const valid = (d) => d && Array.isArray(d.areas) && Array.isArray(d.inventory) && Array.isArray(d.flows);

// Polls `${apiBase}/operations`; falls back to the local model (with the local cold-chain
// simulator's zones) when the API is unset or unreachable. onData(payload, "live" | "simulated").
export function startOpsFeed({ apiBase, data, coldZones = () => [], onData, pollMs = 5000, retryMs = 30000, forceSim = false }) {
  const url = apiBase && !forceSim ? apiBase.replace(/\/+$/, "") + "/operations" : null;
  let nextTry = 0, stopped = false;
  async function tick() {
    if (stopped) return;
    if (url && Date.now() >= nextTry) {
      try {
        const ctl = new AbortController();
        const timer = setTimeout(() => ctl.abort(), 3000);
        const res = await fetch(url, { signal: ctl.signal, cache: "no-store" });
        clearTimeout(timer);
        const d = res.ok ? await res.json() : null;
        if (!valid(d)) throw new Error(`bad response ${res.status}`);
        onData(d, "live");
      } catch (e) {
        console.info("Operations API unreachable, using the local model:", e.message || e);
        nextTry = Date.now() + retryMs;
        onData(simulateOps(data, Date.now(), coldZones()), "simulated");
      }
    } else {
      onData(simulateOps(data, Date.now(), coldZones()), "simulated");
    }
    setTimeout(tick, pollMs);
  }
  tick();
  return () => { stopped = true; };
}
