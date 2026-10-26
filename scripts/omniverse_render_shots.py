"""Render the twin's still cameras in Omniverse RTX (Isaac Sim's Kit), headless.

Runs inside the Isaac Sim container: ./python.sh omniverse_render_shots.py
Inputs come from env vars: USD (a .usdz/.usda, or "test" for a lit cube that checks the GPU
setup), SHOTS (docs/renders/shots.json, or "auto" for
three cameras framed on the stage's bounding box), OUT (folder).
Writes OUT/<shot>/rgb_*.png per shot and OUT/report.json (Kit version, prim types,
whether the particle-field schema is registered, seconds per shot).
"""
import json
import math
import os
import time

from isaacsim import SimulationApp

# Kit picks its default RTX renderer; a named one crashed Kit 107.3 on start in the first trial.
app = SimulationApp({"headless": True})

import carb  # noqa: E402
import omni.replicator.core as rep  # noqa: E402
import omni.usd  # noqa: E402
from pxr import Usd, UsdGeom, UsdLux  # noqa: E402

usd_path, shots_path, out = os.environ["USD"], os.environ["SHOTS"], os.environ["OUT"]
os.makedirs(out, exist_ok=True)
report = {"kit": carb.settings.get_settings().get("/app/version"), "shots": []}

t0 = time.time()
if usd_path == "test":
    omni.usd.get_context().new_stage()
    UsdGeom.Cube.Define(omni.usd.get_context().get_stage(), "/World/Cube").CreateSizeAttr(1.0)
else:
    omni.usd.get_context().open_stage(usd_path)
print("STAGE_OPENED", flush=True)
for _ in range(200):
    app.update()
stage = omni.usd.get_context().get_stage()
report["load_s"] = round(time.time() - t0, 1)

types = {}
for prim in stage.Traverse():
    types[prim.GetTypeName() or "(untyped)"] = types.get(prim.GetTypeName() or "(untyped)", 0) + 1
report["prim_types"] = types
report["particle_field_schema"] = bool(
    Usd.SchemaRegistry().FindConcretePrimDefinition("ParticleField3DGaussianSplat")
)
print("REPORT", json.dumps(report), flush=True)

# The captures carry their own colour; a dome light keeps any lit geometry readable.
dome = UsdLux.DomeLight.Define(stage, "/World/TrialDome")
dome.CreateIntensityAttr(800.0)

def auto_shots(stage):
    box = UsdGeom.BBoxCache(0, ["default", "render"]).ComputeWorldBound(stage.GetPseudoRoot()).ComputeAlignedRange()
    lo, hi = box.GetMin(), box.GetMax()
    c = (lo + hi) / 2
    size = max(hi[0] - lo[0], hi[2] - lo[2])
    centre = [c[0], 1.2, c[2]]
    return [
        {"name": "auto_overview", "eye": [lo[0] - 0.3 * size, hi[1] + 0.6 * size, hi[2] + 0.3 * size], "target": centre, "fov": 60},
        {"name": "auto_side", "eye": [c[0], hi[1] + 0.2 * size, hi[2] + 0.6 * size], "target": centre, "fov": 60},
        {"name": "auto_inside", "eye": [lo[0] + 0.15 * size, 1.7, c[2]], "target": [hi[0], 1.4, c[2]], "fov": 75},
    ]


shots = auto_shots(stage) if shots_path == "auto" or usd_path == "test" else json.load(open(shots_path))["shots"]
APERTURE = 20.955  # mm, Kit's default horizontal aperture
for shot in shots:
    w, h = shot.get("width", 1920), shot.get("height", 1080)
    focal = APERTURE / (2 * math.tan(math.radians(shot["fov"]) / 2))
    cam = rep.create.camera(
        position=tuple(shot["eye"]), look_at=tuple(shot["target"]),
        focal_length=focal, horizontal_aperture=APERTURE,
    )
    rp = rep.create.render_product(cam, (w, h))
    writer = rep.WriterRegistry.get("BasicWriter")
    writer.initialize(output_dir=os.path.join(out, shot["name"]), rgb=True)
    writer.attach([rp])
    t = time.time()
    rep.orchestrator.step(rt_subframes=32)
    rep.orchestrator.wait_until_complete()
    report["shots"].append({"name": shot["name"], "seconds": round(time.time() - t, 1)})
    writer.detach()
    rp.destroy()
    print("SHOT", report["shots"][-1], flush=True)

json.dump(report, open(os.path.join(out, "report.json"), "w"), indent=2)
app.close()
