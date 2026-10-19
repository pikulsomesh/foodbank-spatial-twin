import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "telemetry"))
import app  # noqa: E402
import operations as ops  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
T0 = 1_800_000_000  # a Friday, 03:00 local


def payload(t):
    return ops.build_operations(t, app.build_payload(t)["zones"])


def test_public_copy_matches_lambda_data():
    a = open(os.path.join(ROOT, "src", "telemetry", "operations.json"), "rb").read()
    b = open(os.path.join(ROOT, "public", "ops", "operations.json"), "rb").read()
    assert a == b, "copy src/telemetry/operations.json to public/ops/operations.json"


def test_master_data_is_consistent():
    d = ops.DATA
    assert abs(sum(s["share"] for s in d["sources"]) - 1) < 1e-9
    for s in d["sources"]:
        assert abs(sum(s["mix"].values()) - 1) < 1e-9, s["id"]
    assert abs(sum(c["share"] for c in d["channels"]) - 1) < 1e-9
    assert abs(sum(p["share"] for p in d["kitchen"]["programs"]) - 1) < 1e-9
    zones = {z["id"] for z in d["zones"]}
    assert {c["zone"] for c in d["categories"]} <= zones and {c["zone"] for c in d["channels"]} <= zones
    refs = {r["id"] for r in d["references"]}
    for rec in [d["site"], d["kitchen"], *d["zones"], *d["channels"], *d["kitchen"]["programs"]]:
        assert rec.get("est") or set(rec.get("src", [])) <= refs and rec.get("src"), rec.get("id", rec.get("label"))
    telemetry = {z["id"] for z in app.ZONES}
    assert {z["telemetry_zone"] for z in d["zones"] if z["telemetry_zone"]} <= telemetry


def test_deterministic_and_routed():
    assert payload(T0 + 3600) == payload(T0 + 3600)
    body = json.loads(app.handler({"rawPath": "/operations"}, None)["body"])
    assert {"areas", "inventory", "flows", "kitchen", "trucks", "kpis"} <= body.keys()


def test_stock_carries_over_midnight_and_stays_positive():
    for day in range(ops.math.floor((T0 - 5 * 3600) / 86400), 365 + ops.math.floor((T0 - 5 * 3600) / 86400)):
        p, nxt = ops.day_plan(day), ops.day_plan(day + 1)
        for c in ops.CAT_IDS:
            assert p["cats"][c]["rest"] >= 0, (day, c)
            closing = p["cats"][c]["open"] + p["cats"][c]["in"] - p["cats"][c]["out"]
            assert abs(closing - nxt["cats"][c]["open"]) < 1e-6


def test_mass_balance_within_a_day():
    for dh in range(0, 24 * 3600, 1800):
        d = payload(T0 + 7 * 86400 + dh)
        for a in d["areas"]:
            assert a["on_hand_lbs"] >= 0 and a["utilisation"] < 1, (d["local_time"], a)
        k = d["kpis"]
        assert k["received_lbs"] <= k["received_plan_lbs"] and k["distributed_lbs"] <= k["distributed_plan_lbs"]


def test_sunday_is_closed_and_kitchen_runs_weekdays():
    sunday = T0 + 2 * 86400 + 9 * 3600  # Friday 03:00 + 2 days + 9 h = Sunday 12:00 local
    d = payload(sunday)
    assert not d["open"] and d["kpis"]["received_plan_lbs"] == 0 and d["kitchen"]["meals_planned"] == 0
    fri_noon = payload(T0 + 9 * 3600)
    assert fri_noon["kitchen"]["meals_planned"] > 800
    assert any(b["stage"] != "queued" for b in fri_noon["kitchen"]["batches"])
    evening = payload(T0 + 14 * 3600)
    assert evening["kitchen"]["meals_dispatched"] == evening["kitchen"]["meals_planned"]
