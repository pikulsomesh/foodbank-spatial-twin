#!/usr/bin/env python3
"""Auto-Twin | render the twin's OpenUSD splats in Blender (Cycles, CPU, headless).

Blender 5.2's USD importer brings in the twin's Xform hierarchy but skips the
UsdVolParticleField3DGaussianSplat prims (they arrive as empties), so this
script reads the splats with Blender's bundled `pxr` module and builds one
point cloud per prim: each splat becomes a sphere of its own radius, colour
and opacity, lit as emission so the captured colour is what you see. It is a
point-cloud stand-in for true Gaussian splatting; scripts/render_usd_splats.py
is the faithful splat renderer. Cameras come from the same shots.json.

Run either way (same arguments after `--`):

  blender -b -P scripts/blender_render_twin.py -- public/site.usdz --shots docs/renders/shots.json --out renders/blender
  python scripts/blender_render_twin.py public/site.usdz --shots docs/renders/shots.json --out renders/blender   # pip install bpy usd-core

Add --save-blend twin.blend to keep the scene for interactive work on a Mac.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector
from pxr import Usd, UsdGeom

SPLAT_TYPE = "ParticleField3DGaussianSplat"
SH_C0 = 0.28209479177387814


def usd_to_blender(v: np.ndarray) -> np.ndarray:
    """USD Y-up (x, y, z) -> Blender Z-up (x, -z, y)."""
    v = np.asarray(v, dtype=np.float64)
    return np.stack([v[..., 0], -v[..., 2], v[..., 1]], -1)


def read_splats(path: Path):
    stage = Usd.Stage.Open(str(path))
    mpu = UsdGeom.GetStageMetersPerUnit(stage) or 1.0
    cache = UsdGeom.XformCache()
    for prim in stage.Traverse():
        if prim.GetTypeName() != SPLAT_TYPE:
            continue
        get = lambda name: prim.GetAttribute(name).Get()
        pos = np.asarray(get("positions"), dtype=np.float64)
        scl = np.asarray(get("scales"), dtype=np.float64)
        opa = np.asarray(get("opacities"), dtype=np.float64)
        sh = get("radiance:sphericalHarmonicsCoefficients")
        if sh is not None and len(sh):
            per = ((get("radiance:sphericalHarmonicsDegree") or 0) + 1) ** 2
            rgb = np.clip(0.5 + SH_C0 * np.asarray(sh).reshape(len(pos), per, 3)[:, 0], 0, 1)
        else:
            rgb = np.clip(np.asarray(get("primvars:displayColor")), 0, 1)
        m = np.array(cache.GetLocalToWorldTransform(prim), dtype=np.float64) * mpu
        world = pos @ m[:3, :3] + m[3, :3]
        unit = np.cbrt(abs(np.linalg.det(m[:3, :3])))
        # Geometric mean of the three axes: the twin's splats are isotropic anyway.
        radius = np.cbrt(np.prod(scl, axis=1)) * unit
        name = str(prim.GetPath()).strip("/").replace("/", "_")
        yield name, usd_to_blender(world), radius, rgb, opa


def splat_material() -> bpy.types.Material:
    mat = bpy.data.materials.new("SplatEmission")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    col = nt.nodes.new("ShaderNodeAttribute"); col.attribute_name = "splat_color"
    opa = nt.nodes.new("ShaderNodeAttribute"); opa.attribute_name = "splat_opacity"
    emit = nt.nodes.new("ShaderNodeEmission")
    clear = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(col.outputs["Color"], emit.inputs["Color"])
    nt.links.new(opa.outputs["Fac"], mix.inputs["Fac"])
    nt.links.new(clear.outputs[0], mix.inputs[1])
    nt.links.new(emit.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    return mat


def add_point_cloud(name, pts, radius, rgb, opa, mat, radius_scale: float) -> None:
    """Mesh of vertices carrying radius/colour/opacity, turned into points by Geometry Nodes."""
    mesh = bpy.data.meshes.new(name)
    mesh.vertices.add(len(pts))
    mesh.vertices.foreach_set("co", pts.astype(np.float32).ravel())
    for attr, data, kind in (("splat_radius", radius * radius_scale, "FLOAT"),
                             ("splat_opacity", opa, "FLOAT"),
                             # SH colours are display-referred; Blender wants linear.
                             ("splat_color", np.c_[rgb ** 2.2, np.ones(len(rgb))], "FLOAT_COLOR")):
        a = mesh.attributes.new(attr, kind, "POINT")
        a.data.foreach_set("color" if kind == "FLOAT_COLOR" else "value", data.astype(np.float32).ravel())
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)

    tree = bpy.data.node_groups.new(f"{name}_points", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    n_in = tree.nodes.new("NodeGroupInput")
    n_out = tree.nodes.new("NodeGroupOutput")
    to_pts = tree.nodes.new("GeometryNodeMeshToPoints")
    rad = tree.nodes.new("GeometryNodeInputNamedAttribute")
    rad.data_type = "FLOAT"; rad.inputs["Name"].default_value = "splat_radius"
    set_mat = tree.nodes.new("GeometryNodeSetMaterial"); set_mat.inputs["Material"].default_value = mat
    tree.links.new(n_in.outputs[0], to_pts.inputs["Mesh"])
    tree.links.new(rad.outputs["Attribute"], to_pts.inputs["Radius"])
    tree.links.new(to_pts.outputs["Points"], set_mat.inputs["Geometry"])
    tree.links.new(set_mat.outputs["Geometry"], n_out.inputs[0])
    obj.modifiers.new("Points", "NODES").node_group = tree


def setup_scene(args) -> bpy.types.Object:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = args.samples
    scene.cycles.use_denoising = False
    scene.cycles.max_bounces = 0
    scene.cycles.transparent_max_bounces = 64
    scene.view_settings.view_transform = "Standard"
    scene.render.image_settings.file_format = "JPEG"
    scene.render.image_settings.quality = 92
    world = bpy.data.worlds.new("World"); scene.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.004, 0.005, 0.006, 1)
    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.data.sensor_fit = "HORIZONTAL"
    cam.data.clip_start = 0.05
    return cam


def aim(cam, eye, target, fov_deg: float) -> None:
    e, t = (Vector(usd_to_blender(np.array(v, dtype=float)).tolist()) for v in (eye, target))
    cam.location = e
    cam.rotation_euler = (t - e).to_track_quat("-Z", "Y").to_euler()
    cam.data.angle = math.radians(fov_deg)


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("usd", type=Path)
    ap.add_argument("--shots", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("renders/blender"))
    ap.add_argument("--only", nargs="*", help="render only these shot names")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--samples", type=int, default=16)
    ap.add_argument("--radius-scale", type=float, default=1.7,
                    help="sphere radius in splat sigmas; ~1.5-2 closes gaps without bloating edges")
    ap.add_argument("--save-blend", type=Path)
    args = ap.parse_args(argv)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    cam = setup_scene(args)
    mat = splat_material()
    total = 0
    for name, pts, radius, rgb, opa in read_splats(args.usd.resolve()):
        add_point_cloud(name, pts, radius, rgb, opa, mat, args.radius_scale)
        total += len(pts)
        print(f"[blender] {name}: {len(pts):,} points", flush=True)
    print(f"[blender] {total:,} splats as point spheres", flush=True)
    if args.save_blend:
        bpy.ops.wm.save_as_mainfile(filepath=str(args.save_blend.resolve()))

    scene = bpy.context.scene
    for shot in json.loads(args.shots.read_text())["shots"]:
        if args.only and shot["name"] not in args.only:
            continue
        scene.render.resolution_x = shot.get("width", args.width)
        scene.render.resolution_y = shot.get("height", args.height)
        aim(cam, shot["eye"], shot["target"], shot.get("fov", 60.0))
        scene.render.filepath = str((args.out / f"{shot['name']}.jpg").resolve())
        bpy.ops.render.render(write_still=True)
        print(f"[blender] wrote {scene.render.filepath}", flush=True)


if __name__ == "__main__":
    main()
