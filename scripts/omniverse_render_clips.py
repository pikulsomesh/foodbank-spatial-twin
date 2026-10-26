"""Render short moving clips of the twin in Omniverse RTX (Isaac Sim's Kit), headless.

Runs inside the Isaac Sim container: ./python.sh omniverse_render_clips.py
Env: USD (stage), CLIPS (comma list of clip names below), OUT (folder), FPS (default 24),
SECONDS (default 4), WIDTH/HEIGHT (default 1280x720).
Writes OUT/<clip>/rgb_*.png, one PNG per frame, and OUT/clips.json (seconds per clip).
Encode with: ffmpeg -framerate 24 -i OUT/<clip>/rgb_%04d.png -pix_fmt yuv420p <clip>.mp4

The forklift is facility.usdz's /Facility/ForkliftRig (forks toward +Z in the world frame). It
drives the cross-aisle (z = 11.6) from receiving toward dry storage, then down dry-storage aisle
A1/A2 (x = 19.9); cameras are keyframed per frame. Positions are world metres, Y up.
"""
import json
import math
import os
import time

from isaacsim import SimulationApp

W, H = int(os.environ.get("WIDTH", 1280)), int(os.environ.get("HEIGHT", 720))
app = SimulationApp({"headless": True, "width": W, "height": H})

import omni.replicator.core as rep  # noqa: E402
import omni.usd  # noqa: E402
from pxr import Gf, UsdGeom, UsdLux  # noqa: E402

usd_path, out = os.environ["USD"], os.environ["OUT"]
fps, secs = int(os.environ.get("FPS", 24)), float(os.environ.get("SECONDS", 4))
n = int(fps * secs)
os.makedirs(out, exist_ok=True)

omni.usd.get_context().open_stage(usd_path)
for _ in range(200):
    app.update()
stage = omni.usd.get_context().get_stage()
UsdLux.DomeLight.Define(stage, "/World/ClipDome").CreateIntensityAttr(800.0)
sun = UsdLux.DistantLight.Define(stage, "/World/ClipSun")
sun.CreateIntensityAttr(2500.0)
UsdGeom.XformCommonAPI(sun.GetPrim()).SetRotate(Gf.Vec3f(-55, 35, 0))

cam = UsdGeom.Camera.Define(stage, "/World/ClipCam")
cam.CreateFocalLengthAttr(18.0)
cam.CreateHorizontalApertureAttr(20.955)
cam.CreateVerticalApertureAttr(20.955 * H / W)
cam_xf = UsdGeom.Xformable(cam.GetPrim())
cam_op = cam_xf.MakeMatrixXform()

rig = stage.GetPrimAtPath("/Facility/ForkliftRig")
rig_op = UsdGeom.Xformable(rig).MakeMatrixXform() if rig else None
# /Facility carries the Blender Z-up to Y-up rotation, so world poses are mapped into its frame.
parent = UsdGeom.Xformable(rig.GetParent()).ComputeLocalToWorldTransform(0) if rig else None


def look_at(eye, target):
    # USD cameras look down -Z with +Y up; build the camera-to-world matrix.
    m = Gf.Matrix4d().SetLookAt(Gf.Vec3d(*eye), Gf.Vec3d(*target), Gf.Vec3d(0, 1, 0))
    return m.GetInverse()


def lerp(a, b, t):
    return [a[i] + (b[i] - a[i]) * t for i in range(3)]


def ease(t):
    return t * t * (3 - 2 * t)


def place_rig(x, z, yaw_deg):
    if rig_op:
        # Row vectors: world = local * parent, so local = parent * yaw * translate * parent^-1.
        world = Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(0, 1, 0), yaw_deg))
        world.SetTranslateOnly(Gf.Vec3d(x, 0, z))
        rest = Gf.Matrix4d(parent)
        rest.SetTranslateOnly(Gf.Vec3d(0, 0, 0))
        rig_op.Set(rest * world * parent.GetInverse())


def forklift_chase(t):
    # Chase cam behind and above the forklift along the cross-aisle.
    x = 6 + 9 * ease(t)
    place_rig(x, 11.6, 90)
    return [x - 5.5, 3.2, 11.6], [x + 4, 0.8, 11.6]


def facility_orbit(t):
    place_rig(15 + 4.9 * ease(t), 11.6, 90)
    a = math.radians(200 + 70 * ease(t))
    c = [13, 0, 10]
    return [c[0] + 30 * math.cos(a), 15, c[2] + 30 * math.sin(a)], [c[0], 1.5, c[2]]


def aisle_forklift(t):
    # Down dry-storage aisle between rows A1 and A2, camera following at a distance.
    z = 10.5 - 6.5 * ease(t)
    place_rig(19.9, z, 180)
    return [19.9, 2.8, z + 4.5], [19.9, 1.0, z - 4]


CLIPS = {"forklift_chase": forklift_chase, "facility_orbit": facility_orbit, "aisle_forklift": aisle_forklift}

rp = rep.create.render_product(str(cam.GetPath()), (W, H))
report = {"fps": fps, "frames": n, "size": [W, H], "clips": []}
for name in os.environ["CLIPS"].split(","):
    writer = rep.WriterRegistry.get("BasicWriter")
    writer.initialize(output_dir=os.path.join(out, name), rgb=True, frame_padding=4)
    writer.attach([rp])
    t0 = time.time()
    for i in range(n):
        eye, target = CLIPS[name](i / (n - 1))
        cam_op.Set(look_at(eye, target))
        rep.orchestrator.step(rt_subframes=8)
    rep.orchestrator.wait_until_complete()
    writer.detach()
    report["clips"].append({"name": name, "seconds": round(time.time() - t0, 1)})
    print("CLIP", report["clips"][-1], flush=True)

json.dump(report, open(os.path.join(out, "clips.json"), "w"), indent=2)
app.close()
