#!/usr/bin/env python3
"""Assemble the twin for Omniverse in one frame: public/twin.usda.

The browser places every capture inside the building with public/twin.config.json, and the
telemetry and operations data (zones, forklift, areas, kitchen stations, flows) live in that frame
too. This script makes twin.config.json the single source of truth for placement in USD as well,
choosing the same capture the browser shows:

  /World/Site/<Zone>      the trained (photoreal) 3DGS zone, references zones/<zone>-3dgs.usdz,
                          placed by the capture's `photoreal` block, when photoreal.json lists it;
                          otherwise the World Labs capture, references site.usdz</World/Site/<Zone>>,
                          placed by the capture's own position and yaw_deg. Either way the capture
                          correction inside is unchanged and matches the viewer's
                          rotateX(180) . translate(0, -ground, 0) . scale.
  /World/Operations       references operations.usda (scripts/operations_to_usd.py)
  /World/Facility         references facility.usdz (scripts/build_facility.py), the modeled
                          building and fit-out. It is the visible default; /World/Site, the splat
                          captures, is authored invisible (they only read well from their source
                          cameras) unless --show-captures is given. Toggle it in Omniverse's stage
                          panel to compare.

so Omniverse (or Blender) opens one file with the captures, the operational areas and the
kitchen in the same metres. Nothing is copied: the layer is a few KB of references. The viewer
also crops each capture to its `crop` box (capture frame) so spill outside the room doesn't cover
the building; twin_crop.usdc, a sublayer, does the same in USD by overriding only the opacities of
the splats outside the box to 0 instead of copying every splat.

    python scripts/align_twin_usd.py [--config public/twin.config.json] [--out public/twin.usda] [--atlas] [--no-crop] [--show-captures]
"""
import argparse
import json
import os

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, Vt

ROOT = os.path.join(os.path.dirname(__file__), "..")


def sources(cfg, public, atlas_only=False):
    """[(name, asset, prim path, placement)] for every capture the browser would show."""
    site_name = cfg.get("usdz", "site.usdz")
    site = Usd.Stage.Open(os.path.join(public, site_name)) if os.path.exists(os.path.join(public, site_name)) else None
    library = {c.GetName() for c in site.GetPrimAtPath("/World/Site").GetChildren()} if site else set()
    trained = {}
    pj = os.path.join(public, cfg.get("photoreal", "zones/photoreal.json"))
    if not atlas_only and os.path.exists(pj):
        for z in json.load(open(pj, encoding="utf-8")).get("zones", []):
            usdz = os.path.splitext(z["url"])[0] + ".usdz"
            if os.path.exists(os.path.join(public, usdz)):
                trained[z["name"]] = usdz
    out = []
    for name, c in cfg.get("captures", {}).items():
        if c.get("photoreal") and name in trained:
            out.append((name, trained[name], "/World", c["photoreal"], "photoreal 3DGS"))
        elif name in library and "position" in c:
            out.append((name, site_name, f"/World/Site/{name}", c, "World Labs"))
        else:
            print(f"skip {name}: no capture to place")
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", default=os.path.join(ROOT, "public", "twin.config.json"))
    p.add_argument("--out", default=os.path.join(ROOT, "public", "twin.usda"))
    p.add_argument("--atlas", action="store_true", help="use the World Labs captures even where a trained zone exists")
    p.add_argument("--no-crop", action="store_true", help="skip twin_crop.usdc (show whole captures)")
    p.add_argument("--show-captures", action="store_true", help="leave the splat captures visible over the modeled facility")
    a = p.parse_args()
    public = os.path.dirname(os.path.abspath(a.out))
    cfg = json.load(open(a.config, encoding="utf-8"))

    if os.path.exists(a.out):
        os.remove(a.out)
    stage = Usd.Stage.CreateNew(a.out)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    stage.GetRootLayer().customLayerData = {"autoTwin": {
        "frame": "telemetry frame: metres, Y up, origin at the dock-door corner (same as GET /telemetry and /operations)",
        "placements": os.path.relpath(a.config, public)}}
    site_prim = UsdGeom.Xform.Define(stage, "/World/Site")
    if os.path.exists(os.path.join(public, "facility.usdz")):
        fac = UsdGeom.Xform.Define(stage, "/World/Facility")
        fac.GetPrim().GetReferences().AddReference("./facility.usdz", "/Facility")
        fac.GetPrim().SetCustomDataByKey("source", "scripts/build_facility.py (Blender)")
        if not a.show_captures:
            site_prim.MakeInvisible()

    placed = []
    for name, asset, path, place, kind in sources(cfg, public, a.atlas):
        xf = UsdGeom.Xform.Define(stage, f"/World/Site/{name}")
        prim = xf.GetPrim()
        prim.GetReferences().AddReference(f"./{asset}", path)
        # Author the ops as attributes: a site.usdz zone already has [translate, rotateY] and these
        # stronger opinions replace its layout.json values with the viewer's.
        prim.CreateAttribute("xformOp:translate", Sdf.ValueTypeNames.Double3).Set(Gf.Vec3d(*place.get("position", [0, 0, 0])))
        prim.CreateAttribute("xformOp:rotateY", Sdf.ValueTypeNames.Float).Set(float(place.get("yaw_deg", 0)))
        xf.GetXformOpOrderAttr().Set(["xformOp:translate", "xformOp:rotateY"])
        prim.SetCustomDataByKey("label", cfg["captures"][name].get("title", name))
        prim.SetCustomDataByKey("capture", kind)
        if place.get("crop"):
            for k in ("min", "max"):
                prim.CreateAttribute(f"twin:crop{k.title()}", Sdf.ValueTypeNames.Float3, custom=True).Set(Gf.Vec3f(*place["crop"][k]))
        placed.append((name, place.get("crop"), kind))

    crop_name = "twin_crop.usdc"
    crops = {n: c for n, c, _ in placed if c}
    if crops and not a.no_crop:
        hidden = write_crop_layer(stage, crops, os.path.join(public, crop_name))
        stage.GetRootLayer().subLayerPaths.append(f"./{crop_name}")
        for n, (k, total) in hidden.items():
            print(f"{n}: {k:,} of {total:,} splats outside the crop box hidden")

    if os.path.exists(os.path.join(public, "operations.usda")):
        ops = UsdGeom.Xform.Define(stage, "/World/Operations")
        ops.GetPrim().GetReferences().AddReference("./operations.usda", "/World/Operations")
    stage.GetRootLayer().Save()

    check = Usd.Stage.Open(a.out)
    cache = UsdGeom.XformCache()
    for name, _, kind in placed:
        t = cache.GetLocalToWorldTransform(check.GetPrimAtPath(f"/World/Site/{name}")).ExtractTranslation()
        print(f"{name} ({kind}): origin at ({t[0]:.2f}, {t[1]:.2f}, {t[2]:.2f})")
    print(f"wrote {os.path.relpath(a.out)}: {len(placed)} captures + operations layer")


def write_crop_layer(stage, crops, path):
    """Override opacities to 0 for splats outside each capture's crop box (metres, capture frame:
    Y up, floor at 0, after the rotateX(180) . translate(0, -ground) . scale correction)."""
    if os.path.exists(path):
        os.remove(path)
    layer = Sdf.Layer.CreateNew(path)
    out = Usd.Stage.Open(layer)
    hidden = {}
    t = Usd.TimeCode.Default()
    for name, crop in crops.items():
        zone = stage.GetPrimAtPath(f"/World/Site/{name}")
        to_zone = np.array(UsdGeom.Xformable(zone).ComputeLocalToWorldTransform(t).GetInverse())
        for prim in Usd.PrimRange(zone):
            if prim.GetTypeName() != "ParticleField3DGaussianSplat":
                continue
            pos = np.array(prim.GetAttribute("positions").Get(), dtype=np.float64)
            m = np.array(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(t)) @ to_zone
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
