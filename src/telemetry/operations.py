"""Simulated whole-site operations for the food bank twin: inventory, flows, docks and kitchen.

Like app.py it is stateless and deterministic: every value is a pure function of the wall clock
and the master data in operations.json, so the Lambda needs no database. Stock carries over from
one day to the next (each day's outbound is chosen so stock follows a slow target curve), and
within a day pallets move dock -> sorting -> storage -> staging -> trucks on fixed time windows.
public/js/operations.js is a line-for-line port; tests compare the two payloads.
"""
import json
import math
import os
import time

DATA = json.load(open(os.path.join(os.path.dirname(__file__), "operations.json"), encoding="utf-8"))


def _hash01(key: str) -> float:
    """Repeatable value in [0, 1] for a key. Same FNV-1a + finaliser as operations.js."""
    h = 2166136261
    for ch in key:
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    h = ((h ^ (h >> 15)) * 2246822507) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 3266489909) & 0xFFFFFFFF
    return (h ^ (h >> 16)) / 4294967295


def _noise(key: str) -> float:
    return _hash01(key) * 2 - 1


def _r(v: float, d: int = 0) -> float:
    """Round half up (JS Math.round), not Python's banker's rounding."""
    k = 10 ** d
    return math.floor(v * k + 0.5) / k


def _clamp01(v: float) -> float:
    return min(1.0, max(0.0, v))


def _cum(windows, h: float) -> float:
    """Share of a day's volume moved by hour h, for [start, end, share] windows."""
    return sum(s * _clamp01((h - a) / (b - a)) for a, b, s in windows)


def _inv(windows, p: float) -> float:
    """Hour at which the cumulative share reaches p."""
    acc = 0.0
    for a, b, s in windows:
        if p <= acc + s:
            return a + (p - acc) / s * (b - a)
        acc += s
    return windows[-1][1]


def _clock(h: float) -> str:
    h = h % 24
    hh = math.floor(h)
    return f"{hh:02d}:{math.floor((h - hh) * 60):02d}"


def _weekday(day: int) -> int:
    return (day - 4) % 7  # day 0 of the Unix epoch was a Thursday; 0 = Monday


def _op_days(day: int) -> int:
    """Operating days (Mon-Sat) before `day`; Sunday adds nothing, so stock targets pause."""
    d = day - 4
    return 6 * (d // 7) + min(d % 7, 6)


SITE, FLOW, KITCHEN = DATA["site"], DATA["flow"], DATA["kitchen"]
CATS = DATA["categories"]
CAT_IDS = [c["id"] for c in CATS]
AREAS = {z["id"]: z for z in DATA["zones"]}
CAT_SHARE = {c: sum(s["share"] * s["mix"].get(c, 0) for s in DATA["sources"]) for c in CAT_IDS}
SORT_SHARE = {c: (sum(s["share"] * s["mix"].get(c, 0) for s in DATA["sources"] if s["sorted"]) / CAT_SHARE[c]) for c in CAT_IDS}
BASE_DAY_LBS = SITE["annual_lbs"] / (52 * sum(SITE["operating_weekdays"]))
MEAN_DAY_LBS = SITE["annual_lbs"] / 365
CHANNELS = [c for c in DATA["channels"] if not c.get("from_kitchen")]


def _inbound_day(day: int) -> float:
    return BASE_DAY_LBS * SITE["operating_weekdays"][_weekday(day)] * (1 + 0.08 * _noise(f"in:{day}"))


def _target_stock(i: int, c: dict, day: int) -> float:
    mean = MEAN_DAY_LBS * CAT_SHARE[c["id"]]
    return c["days_of_supply"] * mean * (1 + 0.03 * math.sin(2 * math.pi * _op_days(day) / 29 + i * 1.3))


def _meals(day: int):
    w = _weekday(day)
    meals = _r(KITCHEN["meals_per_day"] * KITCHEN["operating_weekdays"][w] * (1 + 0.05 * _noise(f"meals:{day}")))
    recipe = KITCHEN["recipes"][_op_days(day) % len(KITCHEN["recipes"])]
    return int(meals), recipe


def day_plan(day: int) -> dict:
    """Per-category lbs for one day: inbound, kitchen draw, other outbound, opening stock."""
    meals, recipe = _meals(day)
    inbound = _inbound_day(day)
    plan = {}
    for i, c in enumerate(CATS):
        cid = c["id"]
        s0, s1 = _target_stock(i, c, day), _target_stock(i, c, day + 1)
        cin = inbound * CAT_SHARE[cid]
        out = cin + s0 - s1
        kit = meals * recipe["bom_lbs"].get(cid, 0)
        plan[cid] = {"open": s0, "in": cin, "out": out, "kitchen": kit, "rest": out - kit}
    return {"inbound": inbound, "meals": meals, "recipe": recipe, "cats": plan}


def _kitchen(day: int, h: float, meals: int, recipe: dict, cold: dict) -> dict:
    routing, size = KITCHEN["routing"], KITCHEN["batch_meals"]
    n = math.ceil(meals / size) if meals else 0
    dispatch_a, dispatch_b = KITCHEN["dispatch_window"]
    batches, done = [], {"cook": 0, "chill": 0, "pack": 0}
    in_use = {e["id"]: 0 for e in KITCHEN["equipment"]}
    for i in range(n):
        m = min(size, meals - i * size)
        t = KITCHEN["first_batch_h"] + i * KITCHEN["batch_stagger_h"]
        stage, equipment, start, end = "queued", None, t, t
        for step in routing:
            if step["hours"] is None:  # cold hold until this batch's dispatch slot
                start, end = t, dispatch_a + (i + 1) / n * (dispatch_b - dispatch_a)
            else:
                start, end = t, t + step["hours"]
            eq = step["equipment"] or recipe["cook"]
            if start <= h < end:
                stage, equipment = step["step"], eq
                in_use[eq] = in_use.get(eq, 0) + 1
            if h >= end and step["step"] in done:
                done[step["step"]] += m
            t = end
        if h >= t:
            stage = "dispatched"
        batches.append({"id": f"B{i + 1}", "meals": m, "stage": stage, "equipment": equipment,
                        "start": _clock(KITCHEN["first_batch_h"] + i * KITCHEN["batch_stagger_h"]), "dispatch": _clock(t)})
    dispatched = sum(b["meals"] for b in batches if b["stage"] == "dispatched")
    programs = [{"id": p["id"], "label": p["label"], "meals": int(_r(meals * p["share"]))} for p in KITCHEN["programs"]]
    hold = cold.get(AREAS["kitchen"]["telemetry_zone"])
    return {
        "label": KITCHEN["label"], "open": meals > 0,
        "recipe": {"id": recipe["id"], "label": recipe["label"]} if meals else None,
        "meals_planned": meals, "meals_cooked": done["cook"], "meals_chilled": done["chill"],
        "meals_packed": done["pack"], "meals_dispatched": dispatched,
        "programs": programs, "batches": batches,
        "equipment": [{"id": e["id"], "label": e["label"], "count": e["count"], "in_use": in_use.get(e["id"], 0),
                       "position": e["position"]} for e in KITCHEN["equipment"]],
        "cold_hold": {"temperature_c": hold["temperature_c"], "status": hold["status"]} if hold else None,
    }


def _trucks(day: int, h: float, plan: dict) -> dict:
    inbound, k = [], 0
    if plan["inbound"] > 0:
        loads = []
        for s in DATA["sources"]:
            lbs = plan["inbound"] * s["share"]
            n = max(1, int(_r(lbs / FLOW["lbs_per_inbound_truck"])))
            loads += [(_hash01(f"truck:{day}:{s['id']}:{j}"), s, lbs / n) for j in range(n)]
        loads.sort(key=lambda x: x[0])
        for k, (_, s, lbs) in enumerate(loads):
            eta = _inv(FLOW["inbound_windows"], (k + 0.5) / len(loads)) + 0.2 * _noise(f"eta:{day}:{k}")
            status = "scheduled" if h < eta else "at dock" if h < eta + FLOW["putaway_lag_h"] else "received"
            inbound.append({"id": f"IN-{k + 1:02d}", "source": s["id"], "label": s["label"], "lbs": int(_r(lbs)),
                            "door": k % FLOW["docks"] + 1, "eta": _clock(eta), "status": status})
    rest = sum(c["rest"] for c in plan["cats"].values()) * sum(c["share"] for c in CHANNELS if c["zone"] == "staging")
    n_out = int(_r(rest / FLOW["lbs_per_partner_pickup"])) if rest > 0 else 0
    kinds = ["Pantry", "Shelter", "School pantry", "Senior center", "Mobile pantry"]
    outbound, done = [], 0
    for j in range(n_out):
        eta = _inv(FLOW["outbound_windows"], (j + 0.5) / n_out)
        if h >= eta + 0.25:
            done += 1
            continue
        if len(outbound) < 8:
            agency = 1 + int(_hash01(f"agency:{day}:{j}") * SITE["partner_agencies"]) % SITE["partner_agencies"]
            outbound.append({"id": f"OUT-{j + 1:02d}", "label": f"{kinds[agency % len(kinds)]} {agency:03d}",
                             "lbs": int(_r(rest / n_out)), "door": FLOW["docks"] - j % 5, "eta": _clock(eta),
                             "status": "loading" if h >= eta else "scheduled"})
    return {"inbound": inbound, "outbound_next": outbound, "outbound_done": done, "outbound_planned": n_out}


def build_operations(now: float, cold_zones=None) -> dict:
    """Operations payload at Unix time `now`. `cold_zones` is /telemetry's zones[] (for cold-chain risk)."""
    cold = {z["id"]: z for z in (cold_zones or [])}
    local = now + SITE["utc_offset_hours"] * 3600
    day, h = math.floor(local / 86400), (local % 86400) / 3600
    plan = day_plan(day)
    inb, outb = FLOW["inbound_windows"], FLOW["outbound_windows"]
    R = _cum(inb, h)
    Pd = _cum(inb, h - FLOW["putaway_lag_h"])
    T = _cum(inb, h - FLOW["to_sorting_lag_h"])
    O = _cum(inb, h - FLOW["sorted_lag_h"])
    Co, Cp = _cum(outb, h), _cum(outb, h + 1)  # picked to staging an hour before the truck loads
    ka, kb = FLOW["kitchen_pick_window"]
    Ck = _clamp01((h - ka) / (kb - ka))
    kitchen = _kitchen(day, h, plan["meals"], plan["recipe"], cold)
    disp = kitchen["meals_dispatched"] / plan["meals"] if plan["meals"] else 0.0
    staging_share = sum(c["share"] for c in CHANNELS if c["zone"] == "staging")
    market_share = sum(c["share"] for c in CHANNELS if c["zone"] == "market")

    area_lbs = {a: {c: 0.0 for c in CAT_IDS} for a in AREAS}
    inventory, edges = [], {}

    def edge(a, b, now_lbs, plan_lbs):
        e = edges.setdefault((a, b), [0.0, 0.0])
        e[0] += now_lbs
        e[1] += plan_lbs

    for s in DATA["sources"]:
        edge(s["id"], "receiving", plan["inbound"] * s["share"] * R, plan["inbound"] * s["share"])
    for c in CATS:
        cid, p, f = c["id"], plan["cats"][c["id"]], SORT_SHARE[c["id"]]
        store = c["zone"]
        area_lbs["receiving"][cid] += p["in"] * ((1 - f) * (R - Pd) + f * (R - T))
        area_lbs["sorting"][cid] += p["in"] * f * (T - O)
        on_hand = p["open"] + p["in"] * ((1 - f) * Pd + f * O) - p["kitchen"] * Ck - p["rest"] * Cp
        area_lbs[store][cid] += on_hand
        area_lbs["staging"][cid] += p["rest"] * staging_share * (Cp - Co)
        area_lbs["market"][cid] += p["rest"] * market_share * (Cp - Co)
        area_lbs["kitchen"][cid] += p["kitchen"] * max(0.0, Ck - disp)
        edge("receiving", "sorting", p["in"] * f * T, p["in"] * f)
        edge("receiving", store, p["in"] * (1 - f) * Pd, p["in"] * (1 - f))
        edge("sorting", store, p["in"] * f * O, p["in"] * f)
        edge(store, "kitchen", p["kitchen"] * Ck, p["kitchen"])
        edge(store, "staging", p["rest"] * staging_share * Cp, p["rest"] * staging_share)
        edge(store, "market", p["rest"] * market_share * Cp, p["rest"] * market_share)
        for ch in CHANNELS:
            edge(ch["zone"], ch["id"], p["rest"] * ch["share"] * Co, p["rest"] * ch["share"])
        edge("kitchen", "meals", p["kitchen"] * disp, p["kitchen"])
        mean_out = MEAN_DAY_LBS * CAT_SHARE[cid]
        dos = on_hand / mean_out
        inventory.append({
            "id": cid, "label": c["label"], "zone": store, "on_hand_lbs": int(_r(on_hand)),
            "pallets": int(_r(on_hand / c["lbs_per_pallet"])), "days_of_supply": _r(dos, 1),
            "target_days": c["days_of_supply"], "shelf_life_days": c["shelf_life_days"],
            "received_today_lbs": int(_r(p["in"] * R)), "shipped_today_lbs": int(_r(p["rest"] * Co + p["kitchen"] * Ck)),
            "status": "low" if dos < 0.6 * c["days_of_supply"] else "high" if dos > 1.4 * c["days_of_supply"] else "ok",
        })

    pallet_lbs = {c["id"]: c["lbs_per_pallet"] for c in CATS}
    areas = []
    for a in DATA["zones"]:
        lbs = sum(area_lbs[a["id"]].values())
        pallets = sum(v / pallet_lbs[c] for c, v in area_lbs[a["id"]].items())
        tz = cold.get(a["telemetry_zone"]) if a["telemetry_zone"] else None
        fx, fz = a["footprint"]["x"], a["footprint"]["z"]
        areas.append({
            "id": a["id"], "label": a["label"], "kind": a["kind"], "temp": a["temp"], "footprint": a["footprint"],
            "position": [(fx[0] + fx[1]) / 2, 0, (fz[0] + fz[1]) / 2], "capture": a["capture"],
            "on_hand_lbs": int(_r(lbs)), "pallets": int(_r(pallets)), "capacity_pallets": a["capacity_pallets"],
            "utilisation": _r(pallets / a["capacity_pallets"], 3), "telemetry_zone": a["telemetry_zone"],
            "cold_status": tz["status"] if tz else None,
            "at_risk_lbs": int(_r(lbs)) if tz and tz["status"] == "alarm" else 0,
        })

    flows = [{"from": a, "to": b, "lbs_today": int(_r(v[0])), "lbs_plan": int(_r(v[1]))} for (a, b), v in edges.items()]
    trucks = _trucks(day, h, plan)
    on_hand = sum(i["on_hand_lbs"] for i in inventory)
    distributed = sum(f["lbs_today"] for f in flows if f["to"] in {c["id"] for c in DATA["channels"]})
    distributed_plan = sum(f["lbs_plan"] for f in flows if f["to"] in {c["id"] for c in DATA["channels"]})
    return {
        "site": SITE["name"], "simulated": True,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "local_time": _clock(h), "open": plan["inbound"] > 0,
        "units": {"mass": "lb", "position": "m"},
        "kpis": {
            "received_lbs": int(_r(plan["inbound"] * R)), "received_plan_lbs": int(_r(plan["inbound"])),
            "distributed_lbs": distributed, "distributed_plan_lbs": distributed_plan,
            "meals_equivalent": int(_r(distributed / SITE["lbs_per_meal"])),
            "on_hand_lbs": on_hand, "days_of_supply": _r(on_hand / MEAN_DAY_LBS, 1),
            "meals_planned": kitchen["meals_planned"], "meals_dispatched": kitchen["meals_dispatched"],
            "trucks_received": sum(t["status"] == "received" for t in trucks["inbound"]),
            "trucks_planned": len(trucks["inbound"]),
            "pickups_done": trucks["outbound_done"], "pickups_planned": trucks["outbound_planned"],
            "at_risk_lbs": sum(a["at_risk_lbs"] for a in areas),
        },
        "areas": areas, "inventory": inventory, "flows": flows, "kitchen": kitchen, "trucks": trucks,
    }
