import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "telemetry"))
import app  # noqa: E402


def test_payload_shape_and_determinism():
    a, b = app.build_payload(1_800_000_000.0), app.build_payload(1_800_000_000.0)
    assert a == b
    assert {z["id"] for z in a["zones"]} == {"freezer-1", "cooler-1", "dry-1"}
    assert a["forklifts"][0]["position"] and a["simulated"] is True


def test_freezer_stays_near_setpoint():
    for t in range(0, 7200, 37):
        z = next(z for z in app.build_payload(1.8e9 + t)["zones"] if z["id"] == "freezer-1")
        assert -22 < z["temperature_c"] < -14


def test_handler_routes():
    r = app.handler({"rawPath": "/telemetry"}, None)
    assert r["statusCode"] == 200 and "zones" in json.loads(r["body"])
    assert json.loads(app.handler({"rawPath": "/health"}, None)["body"]) == {"ok": True}
