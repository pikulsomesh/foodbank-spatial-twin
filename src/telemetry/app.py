"""Simulated cold-chain IoT telemetry for the Central Texas Food Bank twin.

Stateless and deterministic: every value is a pure function of the wall clock, so
there is no database to pay for and two callers in the same second see the same data.
Positions are in the twin's metric frame (metres, Y up, origin at the dock-door
corner) so the viewer can pin badges to the 3D scene. Re-calibrate ZONES and
FORKLIFT_PATH once the real factory.usdz extents are known.
"""
import json
import math
import os
import random
import time

SITE = os.environ.get("SITE_NAME", "Central Texas Food Bank")

# id, label, setpoint C, alarm band C (+/-), anchor [x, y, z] in metres
ZONES = [
    {"id": "freezer-1", "label": "Freezer", "setpoint_c": -18.0, "band_c": 3.0, "position": [4.0, 1.5, 3.0]},
    {"id": "cooler-1", "label": "Cooler", "setpoint_c": 3.0, "band_c": 2.5, "position": [12.0, 1.5, 3.0]},
    {"id": "dry-1", "label": "Dry Storage", "setpoint_c": 21.0, "band_c": 6.0, "position": [20.0, 1.5, 8.0]},
]

# Closed loop the forklift drives: dock -> freezer aisle -> cooler aisle -> dry aisle.
FORKLIFT_PATH = [[1.0, 0.0, 12.0], [4.0, 0.0, 6.0], [12.0, 0.0, 6.0], [20.0, 0.0, 10.0], [12.0, 0.0, 12.0]]
LOOP_SECONDS = 120.0


def _noise(key: str, bucket: int) -> float:
    """Repeatable value in [-1, 1] for (sensor, 5-second bucket)."""
    return random.Random(f"{key}:{bucket}").uniform(-1.0, 1.0)


def _zone(z: dict, now: float) -> dict:
    # Slow sinusoidal drift (defrost-like cycle, 15 min) plus per-bucket jitter.
    drift = math.sin(2 * math.pi * now / 900.0 + len(z["id"])) * 0.4 * z["band_c"]
    temp = z["setpoint_c"] + drift + _noise(z["id"], int(now // 5)) * 0.15 * z["band_c"]
    dev = abs(temp - z["setpoint_c"])
    status = "alarm" if dev > z["band_c"] else "warn" if dev > 0.7 * z["band_c"] else "ok"
    humidity = 60.0 + 10.0 * math.sin(2 * math.pi * now / 1800.0) + _noise(z["id"] + "h", int(now // 5)) * 2
    # Door opens roughly 10% of 30-second windows.
    door_open = random.Random(f"{z['id']}:door:{int(now // 30)}").random() < 0.10
    return {
        "id": z["id"],
        "label": z["label"],
        "temperature_c": round(temp, 1),
        "setpoint_c": z["setpoint_c"],
        "humidity_pct": round(humidity, 1),
        "door_open": door_open,
        "status": status,
        "position": z["position"],
    }


def _forklift(now: float) -> dict:
    n = len(FORKLIFT_PATH)
    t = (now % LOOP_SECONDS) / LOOP_SECONDS * n
    i, f = int(t) % n, t - int(t)
    a, b = FORKLIFT_PATH[i], FORKLIFT_PATH[(i + 1) % n]
    pos = [round(a[k] + (b[k] - a[k]) * f, 2) for k in range(3)]
    heading = math.degrees(math.atan2(b[0] - a[0], b[2] - a[2])) % 360
    return {
        "id": "forklift-1",
        "label": "Forklift",
        "position": pos,
        "heading_deg": round(heading, 1),
        "speed_mps": round(sum((b[k] - a[k]) ** 2 for k in range(3)) ** 0.5 * n / LOOP_SECONDS, 2),
        "battery_pct": round(100 - (now % 14400) / 14400 * 70, 1),  # drains 70% over 4 h, then "recharges"
    }


def build_payload(now: float) -> dict:
    zones = [_zone(z, now) for z in ZONES]
    return {
        "site": SITE,
        "simulated": True,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "units": {"temperature": "C", "position": "m", "speed": "m/s"},
        "zones": zones,
        "forklifts": [_forklift(now)],
        "alerts": [f"{z['label']} {z['temperature_c']}C out of range" for z in zones if z["status"] == "alarm"],
    }


def _resp(status: int, body: dict, cache: int = 0) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": f"public, max-age={cache}"},
        "body": json.dumps(body, separators=(",", ":")),
    }


def handler(event, context):
    path = (event or {}).get("rawPath", "/telemetry")
    if path.endswith("/health"):
        return _resp(200, {"ok": True})
    # max-age=2 lets browsers and shared caches absorb a polling viewer's repeat hits.
    return _resp(200, build_payload(time.time()), cache=2)
