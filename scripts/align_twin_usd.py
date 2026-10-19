#!/usr/bin/env python3
"""Assemble the twin for Omniverse in one frame: public/twin.usda.

site.usdz is the zone library the pipeline writes: each capture under /World/Site/<Zone>,
spread out by data/site/layout.json so the zones can be inspected one by one. The browser places
the same captures inside the building with public/twin.config.json, and the telemetry and
operations data (zones, forklift, areas, kitchen stations, flows) live in that frame too. This
script makes twin.config.json the single source of truth for placement in USD as well:

  /World/Site/<Zone>      references site.usdz</World/Site/<Zone>>, re-placed with the viewer's
                          position and yaw_deg (the capture correction inside is unchanged, and
                          matches the viewer's rotateX(180) . translate(0, -ground, 0) . scale)
  /World/Operations       references operations.usda (scripts/operations_to_usd.py)

so Omniverse (or Blender) opens one file with the captures, the operational areas and the
kitchen in the same metres. Nothing is copied: the layer is a few KB of references. The viewer
also crops each capture to its `crop` box (capture frame) so spill outside the room doesn't cover
the building; twin_crop.usdc, a sublayer, does the same in USD by overriding only the opacities of
the splats outside the box to 0 (about 3 MB, instead of copying every splat).

    python scripts/align_twin_usd.py [--config public/twin.config.json] [--out public/twin.usda] [--no-crop]
"""
import argparse
import json
import os

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, Vt

ROOT = os.path.join(os.path.dirname(__file__), "..")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", default=os.path.join(ROOT, "public", "twin.config.json"))
    p.add_argument("--out", default=os.path.join(ROOT, "public", "twin.usda"))
    p.add_argument("--no-crop", action="store_true", help="skip twin_crop.usdc (show whole captures)")
    a = p.parse_args()
    public = os.path.dirname(os.path.abspath(a.out))
    cfg = json.load(open(a.config, encoding="utf-8"))
    site_name = cfg.get("usdz", "site.usdz")
    site = Usd.Stage.Open(os.path.join(public, site_name))
    library = {c.GetName() for c in site.GetPrimAtPath("/World/Site").GetChildren()}

    stage = Usd.Stage.CreateNew(a.out)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    stage.GetRootLayer().customLayerData = {"autoTwin": {
        "frame": "telemetry frame: metres, Y up, origin at the dock-door corner (same as GET /telemetry and /operations)",
        "placements": os.path.relpath(a.config, public), "captures": site_name}}
    UsdGeom.Xform.Define(stage, "/World/Site")

    placed = []
    for name, c in cfg.get("captures", {}).items():
        if name not in library:
            print(f"skip {name}: not in {site_name}")
            continue
        xf = UsdGeom.Xform.Define(stage, f"/World/Site/{name}")
        prim = xf.GetPrim()
        prim.GetReferences().AddReference(f"./{site_name}", f"/World/Site/{name}")
        # The library prim already has [translate, rotateY]; stronger opinions here replace its
        # layout.json values with the viewer's.
        prim.CreateAttribute("xformOp:translate", Sdf.ValueTypeNames.Double3).Set(Gf.Vec3d(*c.get("position", [0, 0, 0])))
        prim.CreateAttribute("xformOp:rotateY", Sdf.ValueTypeNames.Float).Set(float(c.get("yaw_deg", 0)))
        xf.GetXformOpOrderAttr().Set(["xformOp:translate", "xformOp:rotateY"])
        prim.SetCustomDataByKey("label", c.get("title", name))
        if c.get("crop"):
            for k in ("min", "max"):
                prim.CreateAttribute(f"twin:crop{k.title()}", Sdf.ValueTypeNames.Float3, custom=True).Set(Gf.Vec3f(*c["crop"][k]))
        placed.append(name)

    crop_name = "twin_crop.usdc"
    crops = {n: cfg["captures"][n]["crop"] for n in placed if cfg["captures"][n].get("crop")}
    if crops and not a.no_crop:
        hidden = write_crop_layer(site, crops, os.path.join(public, crop_name))
        stage.GetRootLayer().subLayerPaths.append(f"./{crop_name}")
        for n, (k, total) in hidden.items():
            print(f"{n}: {k:,} of {total:,} splats outside the crop box hidden")

    if os.path.exists(os.path.join(public, "operations.usda")):
        ops = UsdGeom.Xform.Define(stage, "/World/Operations")
        ops.GetPrim().GetReferences().AddReference("./operations.usda", "/World/Operations")
    stage.GetRootLayer().Save()

    check = Usd.Stage.Open(a.out)
    cache = UsdGeom.XformCache()
    for name in placed:
        t = cache.GetLocalToWorldTransform(check.GetPrimAtPath(f"/World/Site/{name}")).ExtractTranslation()
        print(f"{name}: origin at ({t[0]:.2f}, {t[1]:.2f}, {t[2]:.2f})")
    print(f"wrote {os.path.relpath(a.out)}: {len(placed)} captures + operations layer")


def write_crop_layer(site, crops, path):
    """Override opacities to 0 for splats outside each capture's crop box (metres, capture frame:
    Y up, floor at 0, after the rotateX(180) . translate(0, -ground) . scale correction)."""
    if os.path.exists(path):
        os.remove(path)
    layer = Sdf.Layer.CreateNew(path)
    out = Usd.Stage.Open(layer)
    hidden = {}
    for name, crop in crops.items():
        zone = site.GetPrimAtPath(f"/World/Site/{name}")
        to_zone = np.array(UsdGeom.Xformable(zone).ComputeLocalToWorldTransform(Usd.TimeCode.Default()).GetInverse())
        for prim in Usd.PrimRange(zone):
            if prim.GetTypeName() != "ParticleField3DGaussianSplat":
                continue
            pos = np.array(prim.GetAttribute("positions").Get(), dtype=np.float64)
            m = np.array(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())) @ to_zone
            local = np.c_[pos, np.ones(len(pos))] @ m  # USD matrices are row-vector
            keep = np.all((local[:, :3] >= crop["min"]) & (local[:, :3] <= crop["max"]), axis=1)
            opa = np.array(prim.GetAttribute("opacities").Get(), dtype=np.float32)
            opa[~keep] = 0.0
            over = out.OverridePrim(prim.GetPath())
            over.CreateAttribute("opacities", Sdf.ValueTypeNames.FloatArray).Set(Vt.FloatArray.FromNumpy(opa))
            hidden[name] = (int((~keep).sum()), len(pos))
    layer.Save()
    return hidden


if __name__ == "__main__":
    main()
