#!/usr/bin/env python3
"""Write the food bank's operations layer as OpenUSD: public/operations.usda.

Every operational area, community-kitchen station and area-to-area flow from
src/telemetry/operations.json becomes a prim keyed by the same ids that GET /operations uses, so
an Omniverse (or Blender) scene can layer it over the captured site and bind live values by id.
Prims sit in the telemetry frame (metres, Y up, origin at the dock-door corner).

Values for a reference moment are authored as attribute defaults so the stage is meaningful
offline; a live extension overwrites twin:onHandLbs, twin:utilisation and twin:lbsToday from the
feed. Needs usd-core (requirements-render.txt).

    python scripts/operations_to_usd.py [--out public/operations.usda] [--at 1800018000]
"""
import argparse
import os
import sys

from pxr import Gf, Sdf, Usd, UsdGeom, Vt

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src", "telemetry"))
import app  # noqa: E402
import operations as ops  # noqa: E402

KIND_COLOR = {"dock": (0.60, 0.65, 0.69), "sorting": (0.85, 0.35, 0.15), "storage": (0.22, 0.53, 0.90),
              "staging": (0.79, 0.52, 0.0), "market": (0.10, 0.62, 0.44), "kitchen": (0.84, 0.32, 0.51)}


def attr(prim, name, vtype, value):
    a = prim.CreateAttribute(f"twin:{name}", vtype, custom=True)
    if value is not None:
        a.Set(value)
    return a


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", default=os.path.join(ROOT, "public", "operations.usda"))
    p.add_argument("--at", type=float, default=1_800_018_000.0, help="Unix time of the authored sample (default: a Friday, 08:00 local)")
    a = p.parse_args()

    snap = ops.build_operations(a.at, app.build_payload(a.at)["zones"])
    areas = {x["id"]: x for x in snap["areas"]}

    stage = Usd.Stage.CreateNew(a.out)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    root = UsdGeom.Xform.Define(stage, "/World/Operations").GetPrim()
    root.SetDocumentation("Food bank operations layer: areas, kitchen stations and flows keyed by GET /operations ids. "
                          "Telemetry frame: metres, Y up, origin at the dock-door corner.")
    attr(root, "feed", Sdf.ValueTypeNames.String, "/operations")
    attr(root, "sampleTime", Sdf.ValueTypeNames.String, snap["timestamp"])
    attr(root, "site", Sdf.ValueTypeNames.String, snap["site"])

    UsdGeom.Scope.Define(stage, "/World/Operations/Areas")
    for z in ops.DATA["zones"]:
        s = areas[z["id"]]
        (x0, x1), (z0, z1) = z["footprint"]["x"], z["footprint"]["z"]
        xf = UsdGeom.Xform.Define(stage, f"/World/Operations/Areas/{z['id']}")
        prim = xf.GetPrim()
        prim.SetDocumentation(z["label"])
        attr(prim, "id", Sdf.ValueTypeNames.String, z["id"])
        attr(prim, "label", Sdf.ValueTypeNames.String, z["label"])
        attr(prim, "kind", Sdf.ValueTypeNames.Token, z["kind"])
        attr(prim, "temp", Sdf.ValueTypeNames.Token, z["temp"])
        attr(prim, "capture", Sdf.ValueTypeNames.String, z["capture"] or "")
        attr(prim, "telemetryZone", Sdf.ValueTypeNames.String, z["telemetry_zone"] or "")
        attr(prim, "capacityPallets", Sdf.ValueTypeNames.Int, z["capacity_pallets"])
        attr(prim, "onHandLbs", Sdf.ValueTypeNames.Float, float(s["on_hand_lbs"]))
        attr(prim, "pallets", Sdf.ValueTypeNames.Int, s["pallets"])
        attr(prim, "utilisation", Sdf.ValueTypeNames.Float, float(s["utilisation"]))
        attr(prim, "estimated", Sdf.ValueTypeNames.Bool, bool(z.get("est")))
        mesh = UsdGeom.Mesh.Define(stage, f"/World/Operations/Areas/{z['id']}/Footprint")
        y = 0.02
        mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(x0, y, z0), Gf.Vec3f(x0, y, z1), Gf.Vec3f(x1, y, z1), Gf.Vec3f(x1, y, z0)]))
        mesh.CreateFaceVertexCountsAttr([4])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
        mesh.CreateExtentAttr([Gf.Vec3f(x0, y, z0), Gf.Vec3f(x1, y, z1)])
        mesh.CreateDoubleSidedAttr(True)
        mesh.CreateDisplayColorAttr([Gf.Vec3f(*KIND_COLOR[z["kind"]])])
        mesh.CreateDisplayOpacityAttr([0.35])

    UsdGeom.Scope.Define(stage, "/World/Operations/Kitchen")
    kitchen = snap["kitchen"]
    for e in kitchen["equipment"]:
        x, _, zz = e["position"]
        w = 0.5 + 0.2 * min(e["count"], 4)
        xf = UsdGeom.Xform.Define(stage, f"/World/Operations/Kitchen/{e['id']}")
        xf.AddTranslateOp().Set(Gf.Vec3d(x, 0.45, zz))
        prim = xf.GetPrim()
        prim.SetDocumentation(e["label"])
        attr(prim, "id", Sdf.ValueTypeNames.String, e["id"])
        attr(prim, "label", Sdf.ValueTypeNames.String, e["label"])
        attr(prim, "count", Sdf.ValueTypeNames.Int, e["count"])
        attr(prim, "inUse", Sdf.ValueTypeNames.Int, e["in_use"])
        cube = UsdGeom.Cube.Define(stage, f"/World/Operations/Kitchen/{e['id']}/Footprint")
        cube.CreateSizeAttr(1.0)
        cube.AddScaleOp().Set(Gf.Vec3f(2 * w, 0.9, 1.4))
        cube.CreateDisplayColorAttr([Gf.Vec3f(*KIND_COLOR["kitchen"])])
        cube.CreateDisplayOpacityAttr([0.5])

    UsdGeom.Scope.Define(stage, "/World/Operations/Flows")
    for f in snap["flows"]:
        if f["from"] not in areas or f["to"] not in areas:
            continue  # sources and channels are off-site; only moves inside the building are drawn
        a0, a1 = areas[f["from"]]["position"], areas[f["to"]]["position"]
        name = f"{f['from']}_to_{f['to']}"
        curve = UsdGeom.BasisCurves.Define(stage, f"/World/Operations/Flows/{name}")
        h = 0.3
        mid = Gf.Vec3f((a0[0] + a1[0]) / 2, h + 1.5, (a0[2] + a1[2]) / 2)
        curve.CreateTypeAttr(UsdGeom.Tokens.linear)
        curve.CreateCurveVertexCountsAttr([3])
        curve.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(a0[0], h, a0[2]), mid, Gf.Vec3f(a1[0], h, a1[2])]))
        curve.CreateWidthsAttr(Vt.FloatArray([max(0.05, f["lbs_plan"] / 40000.0)]))
        curve.SetWidthsInterpolation(UsdGeom.Tokens.constant)
        curve.CreateDisplayColorAttr([Gf.Vec3f(0.85, 0.87, 0.9)])
        prim = curve.GetPrim()
        attr(prim, "from", Sdf.ValueTypeNames.String, f["from"])
        attr(prim, "to", Sdf.ValueTypeNames.String, f["to"])
        attr(prim, "lbsPlan", Sdf.ValueTypeNames.Float, float(f["lbs_plan"]))
        attr(prim, "lbsToday", Sdf.ValueTypeNames.Float, float(f["lbs_today"]))

    stage.GetRootLayer().Save()
    print(f"wrote {os.path.relpath(a.out)}: {len(areas)} areas, {len(kitchen['equipment'])} kitchen stations at {snap['local_time']} local")


if __name__ == "__main__":
    main()
