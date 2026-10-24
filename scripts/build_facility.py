#!/usr/bin/env python3
"""Auto-Twin | model the whole food bank in Blender and export it for the browser and Omniverse.

The captured zones (World Labs and trained 3DGS splats) only read well from their own source
cameras; from anywhere else they are fragments. This script builds the facility as a clean,
lightweight modeled scene instead, in the telemetry frame every other part of the twin uses
(metres, Y up, origin at the dock-door corner, building x 0-26 m by z 0-20 m, community kitchen
annex x 26.5-38 m by z 0-11 m). Footprints come from public/ops/operations.json and the cold-room
probes from the telemetry model, so the 3D badges, the forklift route and the Operations page all
line up with it. Layout and fit-out are a plausible representative food bank inspired by the
walkthrough video (red selective racking, roller-conveyor sorting lines with scales, a client
market of wire shelving), not a replica of any one building.

Outputs (all from one Blender scene):

  public/facility.glb    the browser model (three.js GLTFLoader); one mesh per area and material,
                         so a phone draws it in ~150 calls. The node ForkliftRig is the forklift the
                         viewer animates from telemetry.
  public/facility.usdz   the same scene for OpenUSD / Omniverse, Y up, metres
  --blend PATH           optional .blend to open on a desktop
  --render PATH          optional Cycles still of the overview (CPU, ~minutes)

    pip install bpy pillow numpy          # bpy wheels need Python 3.13
    python scripts/build_facility.py [--out public] [--blend renders/facility.blend] [--render docs/img/facility.jpg]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import bpy

ROOT = Path(__file__).resolve().parent.parent
RNG = np.random.default_rng(7)


# ---------------------------------------------------------------- materials and textures
def srgb(hex_):
    c = [((hex_ >> s) & 255) / 255 for s in (16, 8, 0)]
    return [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]


def make_textures(folder: Path) -> dict[str, Path]:
    """Small procedural textures (JPEG) so the model needs no downloads."""
    out = {}

    def save(name, arr):
        p = folder / f"{name}.jpg"
        Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(p, quality=86)
        out[name] = p

    def noise(n, scale, octaves=4):
        acc = np.zeros((n, n))
        for o in range(octaves):
            k = max(2, int(scale * 2 ** o))
            small = RNG.random((k, k))
            acc += np.array(Image.fromarray((small * 255).astype(np.uint8)).resize((n, n), Image.BICUBIC)) / 255 / 2 ** o
        return acc / sum(1 / 2 ** o for o in range(octaves))

    # Polished concrete, one 4 m slab per tile with a saw-cut joint on two edges.
    n = 1024
    base = 188 + (noise(n, 6) - 0.5) * 26 + (RNG.random((n, n)) - 0.5) * 6
    img = np.stack([base, base + 1, base + 2], -1)
    img[:3, :] *= 0.72
    img[:, :3] *= 0.72
    save("concrete", img)

    # Asphalt.
    base = 70 + (noise(512, 10) - 0.5) * 18 + (RNG.random((512, 512)) - 0.5) * 22
    save("asphalt", np.stack([base, base + 2, base + 4], -1))

    # Quarry tile for the kitchen: 4 x 4 tiles of 0.2 m per 0.8 m repeat.
    n = 512
    t = 0.80 + (noise(n, 4) - 0.5) * 0.12
    img = np.stack([154 * t, 74 * t, 58 * t], -1)
    for i in range(0, n, n // 4):
        img[i:i + 5, :] = (176, 170, 160)
        img[:, i:i + 5] = (176, 170, 160)
    save("quarry", img)

    # Market vinyl tile, 0.6 m squares.
    n = 256
    t = 0.97 + (noise(n, 4) - 0.5) * 0.05
    img = np.stack([222 * t, 216 * t, 204 * t], -1)
    img[:2, :] = img[:, :2] = (190, 184, 172)
    save("vinyl", img)

    # Insulated metal panel walls: 1.2 m panels with a shallow rib.
    w, h = 384, 64
    img = np.full((h, w, 3), 236.0)
    for x in range(0, w, 32):
        img[:, x:x + 2] = 214
    img[:, :4] = 196
    img *= (0.985 + (RNG.random((h, w, 1)) - 0.5) * 0.02)
    save("panel", img)

    # Corrugated carton faces (u 0.6 m, v 0.4 m: a stack of two cartons).
    w, h = 256, 256
    img = np.zeros((h, w, 3)) + (176, 128, 80)
    img *= (0.94 + noise(w, 8)[:h, :w, None] * 0.1)
    img[h // 2 - 3:h // 2 + 3] *= 0.6                      # carton seam
    img[:, w // 2 - 2:w // 2 + 2] *= 0.75
    img[:, w // 2 - 14:w // 2 + 14] = img[:, w // 2 - 14:w // 2 + 14] * 0.6 + np.array([200, 170, 120]) * 0.4  # tape
    for y0 in (40, 168):                                   # printed labels
        img[y0:y0 + 26, 30:96] = (238, 234, 222)
        img[y0 + 8:y0 + 12, 38:88] = (70, 70, 70)
    save("carton", img)

    # Shelf facing of cans (u 0.9 m, v 0.32 m) for market shelving and rack loads.
    w, h = 512, 192
    im = Image.new("RGB", (w, h), (40, 40, 44))
    d = ImageDraw.Draw(im)
    labels = [(196, 40, 36), (232, 184, 48), (38, 112, 64), (220, 220, 214), (40, 86, 160), (210, 110, 30), (130, 30, 40)]
    x = 0
    while x < w:
        run = int(RNG.integers(3, 8))
        col = labels[int(RNG.integers(len(labels)))]
        for _ in range(run):
            cw = 34
            if x + cw > w:
                break
            d.rectangle([x + 1, 22, x + cw - 2, h - 6], fill=(150, 152, 156))
            d.rectangle([x + 1, 46, x + cw - 2, h - 34], fill=col)
            d.rectangle([x + 6, 70, x + cw - 7, 92], fill=tuple(min(255, c + 60) for c in col))
            d.rectangle([x + 1, 22, x + cw - 2, 28], fill=(205, 207, 210))
            x += cw
        x += 2
    save("cans", np.array(im.filter(ImageFilter.GaussianBlur(0.6)), dtype=float))

    # Produce crates seen from above (u, v 0.6 m).
    w = 256
    im = Image.new("RGB", (w, w), (40, 110, 60))
    d = ImageDraw.Draw(im)
    fruit = [(222, 62, 40), (240, 160, 40), (110, 170, 60), (240, 210, 70), (130, 40, 70)]
    for cy in range(2):
        for cx in range(2):
            col = fruit[int(RNG.integers(len(fruit)))]
            x0, y0 = cx * 128 + 8, cy * 128 + 8
            d.rectangle([x0 - 4, y0 - 4, x0 + 116, y0 + 116], fill=(30, 80, 45))
            for _ in range(40):
                px, py = x0 + RNG.integers(0, 104), y0 + RNG.integers(0, 104)
                r = int(RNG.integers(9, 14))
                k = 0.8 + RNG.random() * 0.3
                d.ellipse([px, py, px + r, py + r], fill=tuple(int(min(255, c * k)) for c in col))
    save("produce", np.array(im, dtype=float))

    # Roller conveyor bed (u along the conveyor, 0.5 m).
    w, h = 256, 64
    img = np.zeros((h, w, 3)) + (60, 62, 66)
    for x in range(0, w, 16):
        img[:, x + 2:x + 13] = (178, 182, 188)
        img[:, x + 6:x + 8] = (218, 222, 226)
    save("rollers", img)

    # Stretch-wrapped load: soft vertical sheen.
    w, h = 128, 128
    g = 210 + 30 * np.sin(np.linspace(0, 6 * math.pi, w))[None, :] + (noise(w, 6)[:h] - 0.5) * 20
    save("wrap", np.stack([g, g + 4, g + 10], -1))
    return out


MATERIALS = {
    # name: (hex, roughness, metallic, extra)
    "floor": (0xffffff, 0.42, 0.0, {"tex": "concrete", "tile": (4, 4)}),
    "asphalt": (0xffffff, 0.95, 0.0, {"tex": "asphalt", "tile": (6, 6)}),
    "apron": (0xa9acaf, 0.9, 0.0, {}),
    "kitchen_floor": (0xffffff, 0.55, 0.0, {"tex": "quarry", "tile": (0.8, 0.8)}),
    "market_floor": (0xffffff, 0.5, 0.0, {"tex": "vinyl", "tile": (0.6, 0.6)}),
    "cold_floor": (0xdfe6ea, 0.5, 0.0, {}),
    "wall": (0xffffff, 0.8, 0.0, {"tex": "panel", "tile": (1.2, 0.2)}),
    "wall_ext": (0xc9c2b4, 0.9, 0.0, {}),
    "cap": (0x5b6670, 0.6, 0.3, {}),
    "partition": (0xf1f2f0, 0.85, 0.0, {}),
    "cold_panel": (0xf3f7fa, 0.45, 0.0, {"tex": "panel", "tile": (1.2, 0.2)}),
    "frp": (0xf4f4ef, 0.6, 0.0, {}),
    "dock_door": (0x5c7d9c, 0.55, 0.4, {}),
    "dock_steel": (0x6f777e, 0.45, 0.8, {}),
    "rubber": (0x1d1f22, 0.9, 0.0, {}),
    "yellow": (0xf2c12e, 0.5, 0.0, {}),
    "safety_green": (0x2f9e6a, 0.6, 0.0, {}),
    "line_white": (0xf3f3ef, 0.6, 0.0, {}),
    "paint_grey": (0x9da2a6, 0.8, 0.0, {}),
    "rack_red": (0xc0262b, 0.45, 0.3, {}),
    "rack_beam": (0xe0782a, 0.45, 0.3, {}),
    "wire_deck": (0x9aa1a8, 0.5, 0.7, {}),
    "pallet_wood": (0xb08a5a, 0.85, 0.0, {}),
    "carton": (0xffffff, 0.85, 0.0, {"tex": "carton", "tile": (0.6, 0.4)}),
    "carton_plain": (0xb7875a, 0.85, 0.0, {}),
    "wrap": (0xffffff, 0.25, 0.0, {"tex": "wrap", "tile": (1.2, 1.2)}),
    "cans": (0xffffff, 0.45, 0.2, {"tex": "cans", "tile": (0.9, 0.32)}),
    "produce": (0xffffff, 0.7, 0.0, {"tex": "produce", "tile": (0.6, 0.6)}),
    "crate_green": (0x2e7d4d, 0.6, 0.0, {}),
    "tote_blue": (0x2f6fb5, 0.5, 0.0, {}),
    "steel": (0xc9ced3, 0.28, 0.9, {}),
    "steel_dark": (0x50575e, 0.4, 0.7, {}),
    "black": (0x24272b, 0.5, 0.2, {}),
    "rollers": (0xffffff, 0.35, 0.6, {"tex": "rollers", "tile": (0.5, 0.5)}),
    "glass": (0xbfe3f2, 0.05, 0.0, {"alpha": 0.35}),
    "strip_curtain": (0xa9d8ee, 0.15, 0.0, {"alpha": 0.5}),
    "screen": (0x1b2a36, 0.2, 0.0, {"emit": 0x2b5a78, "emit_strength": 1.0}),
    "sign": (0x17473a, 0.6, 0.0, {}),
    "sign_text": (0xffffff, 0.5, 0.0, {"emit": 0xffffff, "emit_strength": 0.25}),
    "floor_text": (0xf2c12e, 0.6, 0.0, {}),
    "fork_yellow": (0xf5b700, 0.45, 0.1, {}),
    "fork_dark": (0x2a2d31, 0.7, 0.1, {}),
    "truck_white": (0xeef0f2, 0.5, 0.1, {}),
    "truck_cab": (0x1f5f8b, 0.35, 0.5, {}),
    "condenser": (0xb9bec3, 0.5, 0.6, {}),
    "fridge_light": (0xffffff, 0.3, 0.0, {"emit": 0xeaf6ff, "emit_strength": 0.6}),
}


def make_material(name, spec, textures):
    hex_, rough, metal, extra = spec
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*srgb(hex_), 1)
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    if "tex" in extra:
        img = bpy.data.images.load(str(textures[extra["tex"]]))
        tn = nt.nodes.new("ShaderNodeTexImage")
        tn.image = img
        nt.links.new(tn.outputs["Color"], bsdf.inputs["Base Color"])
    if "alpha" in extra:
        bsdf.inputs["Alpha"].default_value = extra["alpha"]
        m.surface_render_method = "BLENDED"
    if "emit" in extra:
        bsdf.inputs["Emission Color"].default_value = (*srgb(extra["emit"]), 1)
        bsdf.inputs["Emission Strength"].default_value = extra["emit_strength"]
    return m


# ---------------------------------------------------------------- geometry builder (telemetry frame)
class Geo:
    def __init__(self):
        self.v, self.faces, self.uv, self.smooth = [], [], [], []


class Builder:
    """Collects faces per (area, material) in the telemetry frame (x, y up, z)."""

    def __init__(self):
        self.parts: dict[tuple[str, str], Geo] = {}
        self.texts = []
        self.area = "Shell"
        self.origin = np.zeros(3)
        self.yaw = 0.0

    # placement of the next primitives: rotate about +Y by yaw (three.js convention), then offset
    def at(self, origin=(0, 0, 0), yaw_deg=0.0):
        self.origin = np.array(origin, float)
        self.yaw = math.radians(yaw_deg)
        return self

    def xf(self, p):
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        x, y, z = p
        return np.array([x * c + z * s, y, -x * s + z * c]) + self.origin

    def face(self, mat, pts, center, smooth=False):
        pts = [np.array(p, float) for p in pts]
        n = np.cross(pts[1] - pts[0], pts[2] - pts[0])
        mid = sum(pts) / len(pts)
        if np.dot(n, mid - np.asarray(center, float)) < 0:
            pts = pts[::-1]
            n = -n
        tile = MATERIALS[mat][3].get("tile", (1, 1))
        ax = int(np.argmax(np.abs(n)))
        ua, va = {0: (2, 1), 1: (0, 2), 2: (0, 1)}[ax]
        uvs = [(p[ua] / tile[0], p[va] / tile[1]) for p in pts]
        g = self.parts.setdefault((self.area, mat), Geo())
        base = len(g.v)
        g.v.extend(self.xf(p) for p in pts)
        g.faces.append(tuple(range(base, base + len(pts))))
        g.uv.extend(uvs)
        g.smooth.append(smooth)

    def box(self, mat, x0, y0, z0, x1, y1, z1, skip=()):
        x0, x1 = min(x0, x1), max(x0, x1)
        y0, y1 = min(y0, y1), max(y0, y1)
        z0, z1 = min(z0, z1), max(z0, z1)
        c = ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
        F = {
            "-x": [(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)],
            "+x": [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)],
            "-y": [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)],
            "+y": [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)],
            "-z": [(x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)],
            "+z": [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)],
        }
        for k, pts in F.items():
            if k not in skip:
                self.face(mat, pts, c)

    def quad_floor(self, mat, x0, z0, x1, z1, y=0.0):
        self.face(mat, [(x0, y, z0), (x0, y, z1), (x1, y, z1), (x1, y, z0)], ((x0 + x1) / 2, y - 1, (z0 + z1) / 2))

    def strip(self, mat, a, b, width, y=0.012):
        """Painted floor line from a to b (x, z)."""
        a, b = np.array(a, float), np.array(b, float)
        d = b - a
        L = np.linalg.norm(d)
        if L < 1e-6:
            return
        nrm = np.array([-d[1], d[0]]) / L * width / 2
        pts = [(p[0], y, p[1]) for p in (a - nrm, a + nrm, b + nrm, b - nrm)]
        self.face(mat, pts, (a[0], y - 1, a[1]))

    def beam(self, mat, a, b, w, h=None):
        """Box of cross-section w x h from point a to point b."""
        h = h or w
        a, b = np.array(a, float), np.array(b, float)
        d = b - a
        L = np.linalg.norm(d)
        d /= L
        up = np.array([0, 1, 0]) if abs(d[1]) < 0.9 else np.array([1, 0, 0])
        s = np.cross(d, up)
        s /= np.linalg.norm(s)
        u = np.cross(s, d)
        corners = [p + s * sx * w / 2 + u * uy * h / 2 for p in (a, b) for sx, uy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        c = (a + b) / 2
        A = corners[:4]
        B = corners[4:]
        self.face(mat, A, c)
        self.face(mat, B, c)
        for i in range(4):
            j = (i + 1) % 4
            self.face(mat, [A[i], A[j], B[j], B[i]], c)

    def cyl(self, mat, c, r, h, n=16, axis="y", cap=True):
        """Cylinder with base centre c, along +axis."""
        c = np.array(c, float)
        ax = {"x": 0, "y": 1, "z": 2}[axis]
        o = [i for i in range(3) if i != ax]

        def pt(t, k):
            p = c.copy()
            p[o[0]] += r * math.cos(t)
            p[o[1]] += r * math.sin(t)
            p[ax] += k * h
            return p
        mid = c.copy()
        mid[ax] += h / 2
        ts = [2 * math.pi * i / n for i in range(n)]
        for i in range(n):
            t0, t1 = ts[i], ts[(i + 1) % n]
            self.face(mat, [pt(t0, 0), pt(t1, 0), pt(t1, 1), pt(t0, 1)], mid, smooth=True)
        if cap:
            self.face(mat, [pt(t, 1) for t in ts], mid)
            self.face(mat, [pt(t, 0) for t in ts], mid)

    def text(self, body, pos, size, *, yaw_deg=0.0, flat=False, mat="sign_text", align="CENTER"):
        """Text mesh. Upright text faces +z (rotated by yaw); flat text lies on the floor reading toward +z."""
        self.texts.append(dict(area=self.area, body=body, pos=self.xf(pos), size=size,
                               yaw=self.yaw + math.radians(yaw_deg), flat=flat, mat=mat, align=align))


# ---------------------------------------------------------------- the facility
B = Builder()
W_H = 7.0          # full wall height
CUT = 1.2          # cutaway wall height on the open sides
T = 0.2            # wall thickness


def wall_x(x, z0, z1, h, gaps=(), mat="wall", t=T, door_h=None):
    """Wall along z at x (thickness to -x), with door gaps [(z0, z1)]: a lintel spans gaps if door_h."""
    edges = [z0] + [g for gap in sorted(gaps) for g in gap] + [z1]
    for a, b in zip(edges[::2], edges[1::2]):
        if b > a:
            B.box(mat, x - t, 0, a, x, h, b)
            B.box("cap", x - t - 0.02, h, a, x + 0.02, h + 0.06, b)
    if door_h:
        for a, b in sorted(gaps):
            B.box(mat, x - t, door_h, a, x, h, b)
            B.box("cap", x - t - 0.02, h, a, x + 0.02, h + 0.06, b)


def wall_z(z, x0, x1, h, gaps=(), mat="wall", t=T, door_h=None):
    edges = [x0] + [g for gap in sorted(gaps) for g in gap] + [x1]
    for a, b in zip(edges[::2], edges[1::2]):
        if b > a:
            B.box(mat, a, 0, z - t, b, h, z)
            B.box("cap", a, h, z - t - 0.02, b, h + 0.06, z + 0.02)
    if door_h:
        for a, b in sorted(gaps):
            B.box(mat, a, door_h, z - t, b, h, z)


CARTON_KINDS = ["carton", "carton", "wrap", "cans", "carton_plain"]


def pallet(x, y, z, w=1.2, d=1.0, h=None, kind=None, yaw=0.0):
    """A wooden pallet with a load; (x, z) is the min corner."""
    B.box("pallet_wood", x, y, z, x + w, y + 0.14, z + d)
    h = h if h is not None else 0.8 + RNG.random() * 0.5
    kind = kind or CARTON_KINDS[int(RNG.integers(len(CARTON_KINDS)))]
    inset = 0.03
    if kind == "cans":
        B.box("cans", x + inset, y + 0.14, z + inset, x + w - inset, y + 0.14 + min(h, 0.96), z + d - inset, skip=("-y",))
    else:
        B.box(kind, x + inset, y + 0.14, z + inset, x + w - inset, y + 0.14 + h, z + d - inset, skip=("-y",))
    return h


def forklift(name_area, origin, yaw):
    """Counterbalance forklift, forks toward +z in its own frame (matches the viewer's heading)."""
    prev = B.area
    B.area = name_area
    B.at(origin, yaw)
    B.box("fork_yellow", -0.55, 0.22, -1.0, 0.55, 0.95, 0.65)          # chassis
    B.box("fork_dark", -0.55, 0.22, -1.25, 0.55, 1.05, -0.95)          # counterweight
    B.box("fork_yellow", -0.5, 0.95, -0.95, 0.5, 1.15, -0.2)           # engine cover / seat base
    B.box("fork_dark", -0.32, 1.15, -0.75, 0.32, 1.55, -0.55)          # seat back
    B.box("fork_dark", -0.3, 1.1, -0.6, 0.3, 1.2, -0.25)               # seat
    for x in (-0.5, 0.5):
        B.beam("fork_dark", (x, 0.95, -0.75), (x, 2.15, -0.7), 0.06)    # overhead guard posts
        B.beam("fork_dark", (x, 0.95, 0.45), (x, 2.15, 0.15), 0.06)
    B.box("fork_dark", -0.55, 2.15, -0.8, 0.55, 2.2, 0.25)              # guard roof
    B.beam("fork_dark", (0.0, 0.95, 0.25), (0.0, 1.25, -0.05), 0.04)    # steering column
    B.cyl("fork_dark", (0, 1.25, -0.08), 0.16, 0.03, n=14, axis="y")    # wheel
    for x in (-0.28, 0.28):
        B.box("steel_dark", x - 0.05, 0.08, 0.68, x + 0.05, 2.45, 0.78)  # mast
    B.box("steel_dark", -0.33, 2.38, 0.68, 0.33, 2.45, 0.78)
    B.box("steel_dark", -0.5, 0.12, 0.8, 0.5, 1.1, 0.86)                 # carriage / backrest
    for x in (-0.3, 0.3):
        B.box("steel_dark", x - 0.06, 0.05, 0.86, x + 0.06, 0.1, 1.95)   # forks
    for x, z, r in ((-0.55, 0.35, 0.28), (0.55, 0.35, 0.28), (-0.55, -0.85, 0.24), (0.55, -0.85, 0.24)):
        B.cyl("rubber", (x - 0.11 if x < 0 else x - 0.11, r, z), r, 0.22, n=16, axis="x")
    B.box("fork_dark", -0.12, 2.2, -0.75, 0.12, 2.3, -0.55)              # beacon base
    B.cyl("yellow", (0, 2.3, -0.65), 0.07, 0.1, n=10)
    B.at()
    B.area = prev


def build():
    # ---------------- site and shell
    B.area = "Site"
    B.quad_floor("asphalt", -26, -8, 46, 34, y=-0.03)
    B.quad_floor("apron", -12, 6.5, 0, 19.5, y=-0.015)
    for z in (8.6, 11.6, 14.6):                                  # truck guide lines
        B.strip("yellow", (-11, z + 1.2), (-1.0, z + 1.2), 0.12, y=-0.005)
    for i in range(7):                                           # car park stripes by the market door
        x = 15 + i * 2.6
        B.strip("paint_grey", (x, 23.0), (x, 27.0), 0.1, y=-0.02)

    B.area = "Shell"
    B.quad_floor("floor", 0, 0, 26, 20)
    # Back wall (full height, exterior tilt-up face behind it).
    wall_z(0.0, -T, 26.0, W_H)
    B.box("wall_ext", -T - 0.05, 0, -T - 0.05, 26.0, W_H, -T)
    # Dock wall (full height) with three dock doors; the middle one is open to a trailer.
    doors = [(8.6, 11.0), (11.6, 14.0), (14.6, 17.0)]
    wall_x(0.0, 0.0, 20.0, W_H, gaps=doors, door_h=3.1)
    B.box("wall_ext", -T - 0.05, 0, 0, -T, W_H, 20.0, skip=())
    for i, (a, b) in enumerate(doors):
        if i != 1:
            B.box("dock_door", -0.12, 0, a, -0.06, 3.1, b)
            for y in np.arange(0.3, 3.1, 0.3):
                B.box("dock_steel", -0.06, y, a, -0.04, y + 0.03, b)
        else:
            B.box("dock_door", -0.14, 2.75, a, -0.04, 3.1, b)   # rolled up
        B.box("dock_steel", 0.0, 0.0, a + 0.1, 2.0, 0.02, b - 0.1)   # leveler
        B.strip("yellow", (0.05, a + 0.05), (2.0, a + 0.05), 0.1)
        B.strip("yellow", (0.05, b - 0.05), (2.0, b - 0.05), 0.1)
        for z in (a - 0.25, b + 0.05):
            B.box("rubber", -0.45, 0.9, z, -T, 1.35, z + 0.2)     # bumpers
        B.box("yellow", -0.25, 3.25, (a + b) / 2 - 0.15, -T, 3.45, (a + b) / 2 + 0.15)  # dock light
    # Open sides: low cutaway walls with doors.
    wall_z(20.0 + T, -T, 26.0, CUT, gaps=[(9.0, 11.0), (22.4, 24.4)])
    wall_x(26.0 + T, 11.0, 20.0, CUT)
    wall_x(26.0 + T, 0.0, 11.0, CUT, gaps=[(8.6, 10.4)])
    # Column bollards at the doors.
    for x, z in ((8.8, 20.2), (11.2, 20.2), (22.2, 20.2), (24.6, 20.2)):
        B.cyl("yellow", (x, 0, z), 0.08, 1.0, n=12)

    # ---------------- floor markings
    B.area = "Markings"
    route = [(1, 12), (4, 6), (12, 6), (20, 10), (12, 12), (1, 12)]
    for a, b in zip(route, route[1:]):
        B.strip("yellow", a, b, 0.07, y=0.011)
    for z in (6.9,):                                              # pedestrian walkway along the cold rooms
        B.strip("safety_green", (1.0, z), (15.5, z), 0.6, y=0.010)
    for x in np.arange(1.4, 15.5, 0.9):
        B.strip("line_white", (x, 6.65), (x, 7.15), 0.35, y=0.012)
    # Painted zone borders from operations.json.
    for zid, (x0, x1, z0, z1) in ZONES.items():
        if zid in ("kitchen", "market", "freezer", "cooler"):
            continue
        pts = [(x0, z0), (x1, z0), (x1, z1), (x0, z1), (x0, z0)]
        for a, b in zip(pts, pts[1:]):
            B.strip("yellow", a, b, 0.1, y=0.012)
    B.text("ORDER STAGING", (8.75, 0.014, 10.95), 0.3, flat=True, mat="floor_text")

    cold_rooms()
    racking()
    staging_and_receiving()
    sorting()
    market()
    kitchen()
    vehicles()
    signage()


def cold_rooms():
    for name, (x0, x1), label, setpoint, load in (("Freezer", (1.0, 7.0), "FREEZER", "-18 °C", "wrap"),
                                                   ("Cooler", (9.0, 15.0), "COOLER", "3 °C", "produce")):
        B.area = name
        z0, z1, h = 0.0, 5.5, 4.2
        B.quad_floor("cold_floor", x0, z0, x1, z1, y=0.006)
        B.box("cold_panel", x0, 0, z0, x0 + 0.15, h, z1)               # side walls
        B.box("cold_panel", x1 - 0.15, 0, z0, x1, h, z1)
        door = ((x0 + x1) / 2 - 0.9, (x0 + x1) / 2 + 0.9)
        B.box("cold_panel", x0, 0, z1 - 0.15, door[0], h, z1)          # front wall
        B.box("cold_panel", door[1], 0, z1 - 0.15, x1, h, z1)
        B.box("cold_panel", door[0], 2.7, z1 - 0.15, door[1], h, z1)
        for x in np.arange(door[0] + 0.02, door[1], 0.2):               # PVC strip curtain
            B.box("strip_curtain", x, 0.05, z1 - 0.1, x + 0.18, 2.7, z1 - 0.08)
        for a, b, c, d in ((x0, x0 + 0.15, z0, z1), (x1 - 0.15, x1, z0, z1), (x0, x1, z1 - 0.15, z1)):
            B.box("steel", a - 0.02, h, c, b + 0.02, h + 0.08, d + 0.02)  # top trim; no roof, a cutaway
        # Racking inside: two levels against the back wall and one side.
        for rx0, rx1, rz0, rz1 in ((x0 + 0.3, x1 - 0.3, 0.25, 1.35),):
            for xx in np.arange(rx0, rx1 + 0.01, (rx1 - rx0) / 4):
                for zz in (rz0, rz1 - 0.08):
                    B.box("rack_red", xx - 0.04, 0, zz, xx + 0.04, 3.4, zz + 0.08)
            for y in (1.55, 3.1):
                for zz in (rz0, rz1 - 0.08):
                    B.box("rack_beam", rx0, y - 0.12, zz, rx1, y, zz + 0.08)
            bay = (rx1 - rx0) / 4
            for i in range(4):
                for y in (0.0, 1.55):
                    pallet(rx0 + i * bay + 0.08, y, rz0 + 0.05, bay - 0.16, 1.0, h=1.0 + RNG.random() * 0.3, kind=load if RNG.random() < 0.6 else "carton")
        for i in range(3):                                              # floor-stacked pallets in the room
            pallet(x0 + 0.5 + i * 1.6, 0, 2.6, 1.2, 1.0, kind=load if i != 1 else "carton")
        # Evaporator on the back wall, condensing unit on the roof edge.
        B.box("condenser", (x0 + x1) / 2 - 0.8, 3.3, 0.05, (x0 + x1) / 2 + 0.8, 3.9, 0.6)
        B.box("steel_dark", (x0 + x1) / 2 - 0.7, 3.4, 0.6, (x0 + x1) / 2 + 0.7, 3.8, 0.62)
        B.box("sign", door[0] - 0.2, 2.95, z1, door[1] + 0.2, 3.75, z1 + 0.04)
        B.text(label, ((x0 + x1) / 2, 3.42, z1 + 0.05), 0.36)
        B.text(setpoint, ((x0 + x1) / 2, 3.04, z1 + 0.05), 0.24)
        B.box("screen", x1 - 0.65, 1.4, z1, x1 - 0.3, 1.7, z1 + 0.03)   # temperature display


def rack_row(x0, depth, z0, z1, aisle_label=None, label_side=+1):
    levels, top = [0.0, 1.7, 3.4, 5.1], 6.4
    bays = round((z1 - z0) / 2.6)
    bl = (z1 - z0) / bays
    for b in range(bays + 1):
        z = z0 + b * bl
        for x in (x0, x0 + depth - 0.08):
            B.box("rack_red", x, 0, z - 0.05, x + 0.08, top, z + 0.05)
        for y in np.arange(0.2, top - 0.6, 1.2):                         # frame bracing
            B.beam("rack_red", (x0 + 0.08, y, z), (x0 + depth - 0.08, y + 1.0, z), 0.035)
            B.beam("rack_red", (x0 + 0.08, y, z), (x0 + depth - 0.08, y, z), 0.03)
        B.box("rack_red", x0 - 0.05, 0, z - 0.12, x0 + depth + 0.05, 0.02, z + 0.12)  # footplate
    for y in levels[1:]:
        for x in (x0, x0 + depth - 0.07):
            B.box("rack_beam", x - 0.01, y - 0.13, z0, x + 0.07, y, z1)
    for b in range(bays):
        z = z0 + b * bl
        for y in levels:
            for k in range(2):
                if RNG.random() < 0.1:
                    continue
                pz = z + 0.1 + k * bl / 2
                pallet(x0 + 0.06, y + (0.0 if y == 0 else 0.0), pz, depth - 0.12, bl / 2 - 0.2,
                       h=min(1.25, 0.9 + RNG.random() * 0.4))
    if aisle_label:
        xs = x0 + depth / 2
        B.box("sign", xs - 0.45, top - 0.55, z1 + 0.06, xs + 0.45, top - 0.05, z1 + 0.1)
        B.text(aisle_label, (xs, top - 0.42, z1 + 0.11), 0.3)


def racking():
    B.area = "DryStorage"
    rack_row(17.2, 1.1, 1.0, 8.6, "A1")
    rack_row(21.5, 1.1, 1.0, 8.6, "A2")
    rack_row(22.75, 1.1, 1.0, 8.6, "A3")
    rack_row(24.75, 1.1, 1.0, 8.6, "A4")


def staging_and_receiving():
    B.area = "Staging"
    for i in range(3):
        for j in range(2):
            x, z = 7.0 + i * 1.4, 8.0 + j * 1.45
            h = pallet(x, 0, z, 1.15, 1.0, kind="wrap", h=1.1 + RNG.random() * 0.35)
            col = ["tote_blue", "safety_green", "yellow"][(i + j) % 3]
            B.box(col, x + 0.35, 0.6, z + 1.0, x + 0.8, 0.9, z + 1.02)  # order tag
    B.area = "Receiving"
    for i in range(2):
        for j in range(4):
            if (i, j) != (0, 3):
                pallet(2.6 + i * 1.25, 0, 13.0 + j * 1.15, 1.1, 1.0)
    for j in range(4):
        pallet(2.6, 0, 8.0 + j * 1.15, 1.1, 1.0, kind="carton")
    # Pallet jack at the open door.
    B.at((1.6, 0, 13.4), 90)
    B.box("yellow", -0.3, 0.05, 0.0, -0.14, 0.12, 1.15)
    B.box("yellow", 0.14, 0.05, 0.0, 0.3, 0.12, 1.15)
    B.box("yellow", -0.32, 0.05, -0.3, 0.32, 0.45, 0.0)
    B.beam("black", (0, 0.45, -0.15), (0, 1.25, -0.55), 0.04)
    B.box("black", -0.2, 1.22, -0.6, 0.2, 1.28, -0.5)
    B.at()
    # Scale and desk by the doors.
    B.box("steel_dark", 0.6, 0, 18.0, 2.2, 0.08, 19.6)
    B.box("yellow", 0.6, 0.08, 18.0, 2.2, 0.1, 18.06)
    B.box("partition", 3.3, 0, 18.6, 4.8, 0.75, 19.4)
    B.box("screen", 3.6, 0.75, 18.9, 4.1, 1.05, 18.94)


def sorting():
    B.area = "Sorting"
    # Room walls (low cutaways) with a forklift door and a volunteer door.
    wall_z(13.5, 5.3, 16.6, CUT, gaps=[(11.3, 14.2)], mat="partition", t=0.12)
    wall_x(5.42, 13.5, 20.0, CUT, gaps=[(15.6, 17.0)], mat="partition", t=0.12)
    wall_x(16.72, 13.5, 20.0, CUT, gaps=[(16.0, 17.4)], mat="partition", t=0.12)
    for z, conveyor in ((14.8, True), (17.6, True)):
        x0, x1 = 6.6, 15.6
        # Tables either side of a roller conveyor.
        for tz in (z - 0.95, z + 0.55):
            B.box("steel", x0, 0.86, tz, x1, 0.92, tz + 0.75)
            for x in np.arange(x0 + 0.1, x1, 1.8):
                for dz in (0.05, 0.65):
                    B.box("steel_dark", x, 0, tz + dz, x + 0.05, 0.86, tz + dz + 0.05)
        if conveyor:
            B.box("rollers", x0 - 0.6, 0.78, z - 0.2, x1 + 0.6, 0.84, z + 0.55)
            B.box("steel_dark", x0 - 0.6, 0.72, z - 0.24, x1 + 0.6, 0.86, z - 0.2)
            B.box("steel_dark", x0 - 0.6, 0.72, z + 0.55, x1 + 0.6, 0.86, z + 0.59)
            for x in np.arange(x0 - 0.4, x1 + 0.6, 1.5):
                B.box("yellow", x, 0, z + 0.1, x + 0.06, 0.78, z + 0.16)
                B.box("black", x - 0.05, 0, z, x + 0.11, 0.08, z + 0.3)
        # Scales on posts and totes / cartons along the tables.
        for i, x in enumerate(np.arange(x0 + 0.6, x1 - 0.3, 1.5)):
            for tz, sgn in ((z - 0.95, 1), (z + 1.3, -1)):
                B.box("steel_dark", x, 0.92, tz + 0.05, x + 0.05, 1.75, tz + 0.1)
                B.box("partition", x - 0.18, 1.6, tz + 0.02, x + 0.23, 1.85, tz + 0.12)
                B.box("screen", x - 0.12, 1.66, tz + (0.12 if sgn > 0 else 0.01), x + 0.17, 1.8, tz + (0.13 if sgn > 0 else 0.02))
                B.box("steel", x - 0.2, 0.92, tz + 0.15, x + 0.25, 0.98, tz + 0.6)
                kind = ("tote_blue", "carton_plain", "produce")[(i + (sgn > 0)) % 3]
                if kind == "produce":
                    B.box("crate_green", x + 0.35, 0.92, tz + 0.12, x + 0.9, 1.18, tz + 0.62, skip=("+y",))
                    B.box("produce", x + 0.38, 1.12, tz + 0.15, x + 0.87, 1.13, tz + 0.59)
                else:
                    B.box(kind, x + 0.35, 0.92, tz + 0.12, x + 0.9, 1.22, tz + 0.62)
    # Gaylord bins of incoming donations at the west end, sorted pallets at the east end.
    for i, z in enumerate((14.0, 15.4, 16.8, 18.2)):
        B.box("pallet_wood", 5.65, 0, z, 6.25, 0.14, z + 1.1)
        B.box("carton_plain", 5.65, 0.14, z, 6.25, 1.1, z + 1.1, skip=("+y",))
        B.box("cans", 5.7, 0.9, z + 0.05, 6.2, 0.91, z + 1.05)
    for i, z in enumerate((14.1, 16.3, 18.5)):
        pallet(15.85, 0, z, 0.8, 1.1, kind="carton")
    for x, z in ((11.2, 13.9), (14.3, 13.9)):
        B.cyl("yellow", (x, 0, z), 0.08, 1.0, n=12)


def market():
    B.area = "Market"
    x0, x1, z0, z1 = 19.0, 26.0, 13.0, 20.0
    B.quad_floor("market_floor", x0, z0, x1, z1, y=0.006)
    wall_z(z0, x0, x1, CUT, gaps=[(20.4, 22.0)], mat="partition", t=0.12)
    wall_x(x0 + 0.12, z0, z1, CUT, gaps=[(15.8, 17.3)], mat="partition", t=0.12)
    # Gondolas of black wire shelving, faces toward the entrance.
    for gz in (14.1, 15.9):
        gx0, gx1 = 20.0, 24.6
        B.box("black", gx0, 0, gz + 0.38, gx1, 1.85, gz + 0.42)      # back panel
        for x in (gx0, gx1 - 0.04):
            B.box("black", x, 0, gz, x + 0.04, 1.85, gz + 0.8)
        for y in (0.12, 0.55, 0.98, 1.41):
            B.box("wire_deck", gx0, y - 0.03, gz, gx1, y, gz + 0.8)
            for x in np.arange(gx0 + 0.05, gx1 - 0.1, 0.92):
                for zz in (gz + 0.03, gz + 0.43):
                    hh = 0.3 + RNG.random() * 0.06
                    B.box("cans", x, y, zz, x + 0.88, y + hh, zz + 0.34)
        B.box("sign", gx1 - 0.02, 1.95, gz + 0.1, gx1 + 0.02, 2.3, gz + 0.7)
    # Produce bins and a refrigerated case.
    for i in range(4):
        x = 20.1 + i * 1.05
        B.box("crate_green", x, 0, 17.7, x + 0.95, 0.75, 18.6)
        B.box("produce", x + 0.04, 0.75, 17.74, x + 0.91, 0.78, 18.56)
    B.box("black", 25.15, 0, 13.4, 25.9, 2.0, 17.6)
    B.box("glass", 25.12, 0.15, 13.45, 25.15, 1.95, 17.55)
    B.box("fridge_light", 25.3, 0.15, 13.5, 25.88, 1.9, 17.5, skip=("+x",))
    for y in (0.5, 0.95, 1.4):
        B.box("cans", 25.3, y, 13.55, 25.75, y + 0.3, 17.45)
    # Checkout desk and carts by the door.
    B.box("partition", 23.4, 0, 18.5, 25.4, 0.95, 19.2)
    B.box("black", 23.35, 0.95, 18.45, 25.45, 0.99, 19.25)
    B.box("screen", 24.0, 0.99, 18.7, 24.45, 1.3, 18.74)
    for i in range(3):
        B.at((20.2 + i * 0.12, 0, 19.0 + i * 0.05), 90)
        B.box("steel", -0.28, 0.45, -0.45, 0.28, 0.95, 0.45, skip=("+y",))
        B.box("black", -0.3, 0.92, -0.62, 0.3, 0.96, -0.58)
        for x in (-0.22, 0.22):
            for z in (-0.4, 0.4):
                B.cyl("black", (x - 0.02, 0.06, z), 0.06, 0.04, n=8, axis="x")
                B.box("steel", x - 0.01, 0.06, z - 0.01, x + 0.01, 0.45, z + 0.01)
        B.at()


def kitchen():
    B.area = "Kitchen"
    x0, x1, z0, z1 = 26.2, 38.0, 0.0, 11.0
    B.quad_floor("kitchen_floor", x0, z0, x1, z1, y=0.004)
    wall_z(0.0, x0, x1 + T, W_H, mat="frp")
    B.box("wall_ext", x0, 0, -T - 0.05, x1 + T, W_H, -T)
    wall_x(x1 + T, 0.0, z1, CUT, mat="frp")
    wall_z(z1 + T, x0, x1 + T, CUT, gaps=[(29.0, 30.4)], mat="frp")
    # Exhaust hood over the cooking line.
    B.box("steel", 27.3, 2.25, 0.0, 35.3, 2.8, 1.5)
    B.box("steel_dark", 27.35, 2.2, 1.35, 35.25, 2.25, 1.5)
    # Steam-jacketed kettles (3), tilt skillets (2), combi ovens (2).
    for x in (28.0, 29.0, 30.0):
        for dx in (-0.3, 0.3):
            B.box("steel", x + dx - 0.03, 0, 1.55, x + dx + 0.03, 0.55, 1.61)
        B.cyl("steel", (x, 0.5, 1.6), 0.42, 0.5, n=20)
        B.cyl("steel_dark", (x, 1.0, 1.6), 0.36, 0.01, n=20)
    for x in (31.05, 32.0):
        B.box("steel", x - 0.42, 0, 1.1, x + 0.42, 0.85, 2.0)
        B.box("steel_dark", x - 0.38, 0.85, 1.14, x + 0.38, 0.95, 1.96, skip=("+y",))
    for x in (33.5, 34.6):
        B.box("steel", x - 0.5, 0, 0.6, x + 0.5, 1.85, 1.55)
        B.box("black", x - 0.42, 0.6, 1.55, x + 0.15, 1.6, 1.57)
        B.box("screen", x + 0.25, 1.2, 1.55, x + 0.42, 1.6, 1.57)
    # Blast chillers along the east wall.
    for z in (4.0, 5.1):
        B.box("steel", 37.0, 0, z, 37.9, 1.95, z + 1.0)
        B.box("black", 36.98, 0.3, z + 0.08, 37.0, 1.7, z + 0.92)
    # Prep / training tables with stools.
    for x in (29.3, 31.6):
        for z in (5.2, 6.9):
            B.box("steel", x - 0.9, 0.86, z - 0.38, x + 0.9, 0.91, z + 0.38)
            B.box("steel", x - 0.85, 0.2, z - 0.33, x + 0.85, 0.23, z + 0.33)
            for dx in (-0.85, 0.82):
                for dz in (-0.33, 0.3):
                    B.box("steel", x + dx, 0, z + dz, x + dx + 0.04, 0.86, z + dz + 0.04)
            for dx in (-0.5, 0.0, 0.5):
                B.box("partition", x + dx - 0.2, 0.91, z - 0.15, x + dx + 0.2, 0.93, z + 0.15)
    # Portion and pack line.
    B.box("rollers", 31.2, 0.82, 8.8, 35.4, 0.88, 9.35)
    B.box("steel", 31.2, 0, 8.8, 35.4, 0.82, 9.35, skip=("+y",))
    B.box("steel", 31.3, 0.88, 9.6, 35.3, 0.92, 10.3)
    for x in np.arange(31.5, 35.2, 0.55):
        B.box("tote_blue", x, 0.88, 8.95, x + 0.4, 0.98, 9.2)
    # Walk-in cooler.
    B.box("cold_panel", 35.9, 0, 7.4, 38.0, 2.7, 10.9)
    B.box("steel", 35.88, 0.1, 8.6, 35.9, 2.1, 9.6)
    B.box("screen", 35.87, 1.5, 9.75, 35.89, 1.75, 10.05)
    # Hand sink, wall shelving.
    B.box("steel", 26.4, 0.75, 3.0, 26.9, 0.95, 3.6)
    B.box("wire_deck", 26.3, 1.5, 4.2, 26.75, 1.53, 7.6)
    B.box("wire_deck", 26.3, 1.9, 4.2, 26.75, 1.93, 7.6)
    for y in (1.53, 1.93):
        B.box("carton_plain", 26.35, y, 4.3, 26.7, y + 0.3, 7.5)
    B.box("sign", 29.2, 4.2, 0.0, 35.0, 5.0, 0.05)
    B.text("COMMUNITY KITCHEN", (32.1, 4.45, 0.06), 0.42)


def vehicles():
    # The viewer drives ForkliftRig from telemetry; a second forklift is parked at its charger.
    forklift("ForkliftRig", (0, 0, 0), 0)
    forklift("Vehicles", (8.0, 0, 2.2), 180)
    B.area = "Vehicles"
    B.box("steel_dark", 7.5, 0.6, 0.0, 8.5, 1.5, 0.35)
    B.box("screen", 7.7, 1.1, 0.35, 8.3, 1.35, 0.37)
    # Trailer at the open dock door with its tractor.
    zc = 12.8
    B.box("truck_white", -13.2, 1.15, zc - 1.27, -0.3, 4.1, zc + 1.27)
    B.box("steel_dark", -13.0, 0.9, zc - 1.1, -0.5, 1.15, zc + 1.1)
    for x in (-11.8, -10.6):
        for s in (-1, 1):
            B.cyl("rubber", (x, 0.5, zc + s * 1.05 - (0.3 if s > 0 else 0)), 0.5, 0.3, n=16, axis="z")
    B.box("steel_dark", -3.0, 0, zc - 1.0, -2.8, 1.0, zc - 0.9)
    B.box("truck_cab", -17.6, 0.9, zc - 1.25, -14.2, 3.4, zc + 1.25)
    B.box("glass", -17.65, 2.2, zc - 1.1, -17.6, 3.2, zc + 1.1)
    B.box("steel_dark", -14.2, 0.9, zc - 1.0, -12.6, 1.3, zc + 1.0)
    for x in (-16.8, -14.0, -13.0):
        for s in (-1, 1):
            B.cyl("rubber", (x, 0.5, zc + s * 1.05 - (0.3 if s > 0 else 0)), 0.5, 0.3, n=16, axis="z")
    B.text("FOOD BANK", (-6.8, 2.45, zc + 1.28), 0.62, mat="sign")
    B.text("Nourishing our community", (-6.8, 1.85, zc + 1.28), 0.3, mat="sign")


def signage():
    B.area = "Signage"
    for label, x in (("DRY STORAGE", 21.5),):
        B.box("sign", x - 2.4, 6.45, 0.0, x + 2.4, 6.95, 0.05)
        B.text(label, (x, 6.56, 0.06), 0.34)
    B.box("sign", 2.2, 5.0, 0.0, 14.0, 5.8, 0.05)
    B.text("COLD STORAGE", (8.1, 5.22, 0.06), 0.44)
    B.box("sign", 0.0, 4.2, 4.4, 0.05, 5.0, 7.6)
    B.text("DOCKS 1-3", (0.06, 4.45, 6.0), 0.36, yaw_deg=90)
    # Market and sorting: hanging name boards on the partition corners (low, facing the overview).
    B.box("sign", 23.4, 1.25, 20.0 + T, 25.6, 1.75, 20.0 + T + 0.05)
    B.text("CLIENT MARKET", (24.5, 1.38, 20.0 + T + 0.06), 0.24)
    B.box("sign", 6.2, 1.25, 20.0 + T, 9.2, 1.75, 20.0 + T + 0.05)
    B.text("VOLUNTEER SORTING", (7.7, 1.38, 20.0 + T + 0.06), 0.24)


ZONES = {}


# ---------------------------------------------------------------- Blender scene
def tb(p):
    """Telemetry frame (Y up) -> Blender (Z up). The glTF exporter maps it back exactly."""
    return (p[0], -p[2], p[1])


def build_scene(textures):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mats = {k: make_material(k, v, textures) for k, v in MATERIALS.items()}
    roots = {}

    def root(area):
        if area not in roots:
            e = bpy.data.objects.new(area, None)
            bpy.context.scene.collection.objects.link(e)
            roots[area] = e
        return roots[area]

    for (area, mat), g in sorted(B.parts.items()):
        if not g.faces:
            continue
        me = bpy.data.meshes.new(f"{area}_{mat}")
        me.from_pydata([tb(v) for v in g.v], [], g.faces)
        uvl = me.uv_layers.new(name="UVMap")
        for i, loop in enumerate(me.loops):
            uvl.data[i].uv = g.uv[loop.vertex_index]
        for poly, sm in zip(me.polygons, g.smooth):
            poly.use_smooth = sm
        me.materials.append(mats[mat])
        me.validate()
        ob = bpy.data.objects.new(f"{area}_{mat}", me)
        bpy.context.scene.collection.objects.link(ob)
        ob.parent = root(area)

    for i, t in enumerate(B.texts):
        cu = bpy.data.curves.new(f"text{i}", "FONT")
        cu.body = t["body"]
        cu.size = t["size"]
        cu.align_x = t["align"]
        cu.align_y = "BOTTOM_BASELINE"
        ob = bpy.data.objects.new(f"{t['area']}_text_{i}", cu)
        bpy.context.scene.collection.objects.link(ob)
        x, y, z = tb(t["pos"])
        ob.location = (x, y, z)
        if t["flat"]:
            ob.rotation_euler = (0, 0, t["yaw"])
        else:
            ob.rotation_euler = (math.pi / 2, 0, t["yaw"])
        bpy.context.view_layer.objects.active = ob
        for o in bpy.context.scene.objects:
            o.select_set(o == ob)
        bpy.ops.object.convert(target="MESH")
        ob = bpy.context.view_layer.objects.active
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        ob.data.materials.clear()
        ob.data.materials.append(mats[t["mat"]])
        ob.parent = root(t["area"])

    # Join texts by area and material so the browser gets few draw calls.
    by = {}
    for ob in list(bpy.context.scene.objects):
        if "_text_" in ob.name:
            by.setdefault((ob.parent.name, ob.data.materials[0].name), []).append(ob)
    for (area, mat), obs in by.items():
        for o in bpy.context.scene.objects:
            o.select_set(o in obs)
        bpy.context.view_layer.objects.active = obs[0]
        if len(obs) > 1:
            bpy.ops.object.join()
        bpy.context.view_layer.objects.active.name = f"{area}_{mat}_text"
    return roots


def add_render_setup(path):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = int(os.environ.get("SAMPLES", 48))
    sc.cycles.use_denoising = True
    sc.render.resolution_x, sc.render.resolution_y = 1600, 900
    sc.view_settings.view_transform = "AgX"
    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.55, 0.62, 0.72, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.9
    sc.world = world
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    sun.data.energy = 3.2
    sun.data.angle = math.radians(8)
    sun.rotation_euler = (math.radians(40), math.radians(18), math.radians(-35))
    sc.collection.objects.link(sun)
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    cam.data.lens = 30
    sc.collection.objects.link(cam)
    target = np.array(tb((18.0, 0.0, 8.0)))
    pos = np.array(tb((14.0, 30.0, 40.0)))
    cam.location = pos
    d = target - pos
    from mathutils import Vector
    cam.rotation_euler = Vector(d).to_track_quat("-Z", "Y").to_euler()
    sc.camera = cam
    sc.render.filepath = str(path)
    sc.render.image_settings.file_format = "JPEG"
    bpy.ops.render.render(write_still=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "public"))
    ap.add_argument("--ops", default=str(ROOT / "public" / "ops" / "operations.json"))
    ap.add_argument("--blend")
    ap.add_argument("--render")
    a = ap.parse_args()

    for z in json.load(open(a.ops, encoding="utf-8"))["zones"]:
        ZONES[z["id"]] = (*z["footprint"]["x"], *z["footprint"]["z"])
    build()
    tris = sum(len(f) - 2 for g in B.parts.values() for f in g.faces)
    print(f"built {len(B.parts)} area/material parts, {tris:,} triangles before text")

    out = Path(a.out)
    with tempfile.TemporaryDirectory() as tmp:
        textures = make_textures(Path(tmp))
        build_scene(textures)
        for o in bpy.context.scene.objects:
            o.select_set(False)
        glb = out / "facility.glb"
        bpy.ops.export_scene.gltf(filepath=str(glb), export_format="GLB", export_yup=True, export_apply=True,
                                  export_image_format="JPEG", export_extras=False, export_cameras=False,
                                  export_lights=False)
        print(f"wrote {glb} ({glb.stat().st_size / 1e6:.1f} MB)")
        usdz = out / "facility.usdz"
        bpy.ops.wm.usd_export(filepath=str(usdz), export_materials=True, export_textures_mode="NEW",
                              convert_orientation=True, export_global_forward_selection="NEGATIVE_Z",
                              export_global_up_selection="Y", root_prim_path="/Facility")
        print(f"wrote {usdz} ({usdz.stat().st_size / 1e6:.1f} MB)")
        if a.blend:
            bpy.ops.file.pack_all()
            bpy.ops.wm.save_as_mainfile(filepath=str(Path(a.blend).resolve()))
        if a.render:
            add_render_setup(Path(a.render).resolve())


if __name__ == "__main__":
    main()
