"""Square (1080x1080) LinkedIn video: video, world model, Omniverse, AWS pattern, live view, end card.

Usage: python3 make_square_video.py [--root /mnt/project-files] [--diagram path/to/arch_aws.png] [out.mp4]
Reads existing footage from <root>/linkedin/video/clean and the architecture diagram; nothing is fetched.
Needs Pillow, numpy and ffmpeg.
"""
import argparse
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

S, FPS, XF = 1080, 24, 10  # size, frame rate, crossfade frames
RED = (163, 31, 52)
INK = (34, 34, 36)
GRAY = (110, 112, 116)
LIGHT = (226, 227, 229)
BG = (255, 255, 255)
PANEL = (40, 236, 1040, 876)  # x0, y0, x1, y1 (1000 x 640)
FONT = "/usr/share/fonts/opentype/inter/Inter-{}.otf"
STEPS = ["Video", "World model", "Omniverse", "AWS pattern", "Live view"]
TM = ("NVIDIA, NVIDIA Omniverse, AWS, World Labs, Marble, OpenUSD, Blender and KXXV are trademarks of their "
      "respective owners, named only to identify tools and sources. No affiliation or endorsement is implied.")

_fonts = {}


def font(size, weight="Regular"):
    key = (size, weight)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(FONT.format(weight), size)
    return _fonts[key]


def ease(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def wrap(d, text, f, width):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if d.textlength(t, font=f) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def fade_text(img, xy, text, f, fill, t, delay=0.0, width=S - 80, gap=1.25):
    """Text that fades in and rises 12 px, starting `delay` seconds into the segment."""
    a = ease((t - delay) / 0.5)
    if a <= 0:
        return xy[1]
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x, y = xy
    y0 = y + int(12 * (1 - a))
    for line in wrap(d, text, f, width):
        d.text((x, y0), line, font=f, fill=fill + (int(255 * a),))
        y0 += int(f.size * gap)
        y += int(f.size * gap)
    img.alpha_composite(layer)
    return y


def rounded_paste(img, tile, box, r=18):
    mask = Image.new("L", tile.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, tile.width - 1, tile.height - 1), r, fill=255)
    img.paste(tile, box[:2], mask)


def cover(im, w, h):
    sw, sh = im.size
    s = max(w / sw, h / sh)
    im = im.resize((round(sw * s), round(sh * s)), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


def video_frames(path, seconds, speed=1.0):
    """Frames of a clip resampled to FPS for `seconds`, slowed by `speed` < 1; holds the last frame."""
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, check=True).stdout.strip().split(",")
    w, h = int(probe[0]), int(probe[1])
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"fps={FPS}",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    n = len(raw) // (w * h * 3)
    src = [Image.frombuffer("RGB", (w, h), raw[i * w * h * 3:(i + 1) * w * h * 3]) for i in range(n)]
    out = []
    for k in range(int(seconds * FPS)):
        out.append(src[min(int(k * speed), n - 1)])
    return out


def chip(d, xy, text, fill=RED, ink=(255, 255, 255)):
    f = font(20, "SemiBold")
    x, y = xy
    w = d.textlength(text, font=f)
    d.rounded_rectangle((x, y, x + w + 26, y + 36), 18, fill=fill)
    d.text((x + 13, y + 7), text, font=f, fill=ink)


def progress(d, step):
    w = (S - 80 - 4 * 12) / 5
    for i, name in enumerate(STEPS):
        x = 40 + i * (w + 12)
        on = i == step
        d.rectangle((x, 1030, x + w, 1033), fill=RED if on else LIGHT)
        d.text((x, 1000), name, font=font(17, "SemiBold" if on else "Medium"), fill=INK if on else GRAY)


def step_frame(step, title, sub, panel, t, source, chips=()):
    img = Image.new("RGBA", (S, S), BG + (255,))
    d = ImageDraw.Draw(img)
    d.text((40, 52), f"0{step + 1}", font=font(22, "Bold"), fill=RED)
    d.text((78, 52), STEPS[step].upper(), font=font(22, "SemiBold"), fill=GRAY)
    y = fade_text(img, (40, 92), title, font(48, "SemiBold"), INK, t)
    fade_text(img, (40, y + 2), sub, font(26), GRAY, t, delay=0.15)
    rounded_paste(img, panel, PANEL)
    d = ImageDraw.Draw(img)
    cx = PANEL[0] + 20
    for c in chips:
        chip(d, (cx, PANEL[1] + 20), c)
        cx += d.textlength(c, font=font(20, "SemiBold")) + 40
    fade_text(img, (40, 896), source, font(19), GRAY, t, delay=0.3, width=S - 80, gap=1.35)
    progress(d, step)
    return img.convert("RGB")


def clip_segment(step, title, sub, clips, source, chips=()):
    pw, ph = PANEL[2] - PANEL[0], PANEL[3] - PANEL[1]
    frames = []
    for c in clips:
        frames += video_frames(*c)
    for k, fr in enumerate(frames):
        yield step_frame(step, title, sub, cover(fr, pw, ph), k / FPS, source, chips)


def diagram_segment(diagram):
    """Ken Burns tour of the architecture diagram with a caption per stop."""
    im = Image.open(diagram).convert("RGB")
    W, H = im.size
    pw, ph = PANEL[2] - PANEL[0], PANEL[3] - PANEL[1]
    aspect = pw / ph
    # stops: (centre x, centre y, width) as fractions of the diagram, hold seconds, caption
    full = (0.5, 0.5, 1.0)
    stops = [
        (full, 1.6, "Three steps: video to twin, connect the data, visualize. Solid is built, dashed is designed."),
        ((0.17, 0.15, 0.36), 2.0, "Step 1: video frames, a world model and a Blender model become one OpenUSD twin."),
        ((0.18, 0.51, 0.37), 2.6, "OT zones: sensors and gateways on their own network. Data only flows out; "
                                  "nothing comes in except signed, approved firmware."),
        ((0.50, 0.50, 0.36), 2.3, "AWS ingest: per-device certificates, IoT Core, Lambda and DynamoDB, "
                                  "with Device Defender watching the fleet."),
        ((0.75, 0.38, 0.36), 2.3, "A GET-only API behind a read-only boundary: no write path back to the floor."),
        (full, 1.8, "Built today: the read-only API, on simulated data. Device-side controls are a design."),
    ]
    move = 0.7
    timeline = []
    for i, (box, hold, cap) in enumerate(stops):
        if i:
            prev = stops[i - 1][0]
            for k in range(int(move * FPS)):
                a = ease(k / (move * FPS))
                timeline.append((tuple(p + (q - p) * a for p, q in zip(prev, box)), cap, i))
        for k in range(int(hold * FPS)):
            timeline.append((box, cap, i))
    starts = {}
    for k, (_, _, i) in enumerate(timeline):
        starts.setdefault(i, k)
    for k, ((cx, cy, fw), cap, i) in enumerate(timeline):
        cw = fw * W
        ch = cw / aspect
        if ch > H:  # full view: fit the whole diagram, letterboxed on white
            tile = Image.new("RGB", (pw, ph), (255, 255, 255))
            sc = min(pw / W, ph / H)
            small = im.resize((round(W * sc), round(H * sc)), Image.LANCZOS)
            tile.paste(small, ((pw - small.width) // 2, (ph - small.height) // 2))
        else:
            x0 = min(max(cx * W - cw / 2, 0), W - cw)
            y0 = min(max(cy * H - ch / 2, 0), H - ch)
            tile = im.crop((round(x0), round(y0), round(x0 + cw), round(y0 + ch))).resize((pw, ph), Image.LANCZOS)
        framed = Image.new("RGB", (pw, ph), LIGHT)
        framed.paste(tile.crop((2, 2, pw - 2, ph - 2)), (2, 2))
        t_cap = (k - starts[i]) / FPS
        yield caption_frame(framed, cap, t_cap, k / FPS)


def caption_frame(panel, cap, t_cap, t):
    img = step_frame(3, "An AWS architecture pattern", "Built for operational technology, not only IT",
                     panel, t, "")
    img = img.convert("RGBA")
    fade_text(img, (40, 896), cap, font(22, "Medium"), INK, t_cap, width=S - 80, gap=1.35)
    return img.convert("RGB")


def title_segment(seconds=3.0):
    for k in range(int(seconds * FPS)):
        t = k / FPS
        img = Image.new("RGBA", (S, S), BG + (255,))
        d = ImageDraw.Draw(img)
        bar = int(120 * ease(t / 0.6))
        d.rectangle((80, 380, 80 + bar, 386), fill=RED)
        y = fade_text(img, (80, 416), "From video to digital twin", font(64, "SemiBold"), INK, t, 0.1, S - 160, 1.12)
        y = fade_text(img, (80, y + 18), "A food bank twin built from one walkthrough video, with world models, "
                      "AWS and NVIDIA Omniverse", font(30), GRAY, t, 0.35, S - 160)
        fade_text(img, (80, 960), "Representative facility. All sensor data is simulated.",
                  font(20, "Medium"), GRAY, t, 0.6, S - 160)
        yield img.convert("RGB")


def end_segment(seconds=5.0):
    for k in range(int(seconds * FPS)):
        t = k / FPS
        img = Image.new("RGBA", (S, S), BG + (255,))
        d = ImageDraw.Draw(img)
        d.rectangle((80, 250, 80 + int(120 * ease(t / 0.6)), 256), fill=RED)
        y = fade_text(img, (80, 286), "From video to digital twin", font(64, "SemiBold"), INK, t, 0.0, S - 160, 1.12)
        y += 40
        for i, (head, sub) in enumerate([("Paper on arXiv", "An exploratory study of world models and Gaussian "
                                                            "splatting on in-the-wild video"),
                                         ("Pipeline open source on GitHub", "Video to OpenUSD twin, AWS pattern, "
                                                                            "browser viewer")]):
            a = ease((t - 0.3 - 0.25 * i) / 0.5)
            if a > 0:
                d.rectangle((80, y + 8, 86, y + 92), fill=RED + (int(255 * a),))
            yy = fade_text(img, (108, y), head, font(36, "SemiBold"), INK, t, 0.3 + 0.25 * i, S - 200)
            y = fade_text(img, (108, yy), sub, font(24), GRAY, t, 0.35 + 0.25 * i, S - 200) + 36
        fade_text(img, (80, y + 10), "Link in the post", font(26, "SemiBold"), RED, t, 0.9, S - 160)
        fade_text(img, (80, 880), "Representative food bank, not a replica of any building. All data simulated; "
                  "only the read-only side is built.", font(19, "Medium"), GRAY, t, 1.0, S - 160, 1.35)
        fade_text(img, (80, 960), TM, font(15), GRAY, t, 1.0, S - 160, 1.35)
        yield img.convert("RGB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default=str(Path(__file__).with_name("foodbank-twin-square.mp4")))
    ap.add_argument("--root", default="/mnt/project-files")
    ap.add_argument("--diagram", default=None)
    a = ap.parse_args()
    clean = Path(a.root) / "linkedin/video/clean"
    diagram = a.diagram or str(Path(a.root) / "paper/diagrams/arch_aws.png")

    segments = [
        title_segment(),
        clip_segment(0, "It starts with a walkthrough video", "A publicly available news tour of a Texas food bank",
                     [(clean / "news_kxxv_tour_398s.mp4", 4.0)],
                     "Footage: 25 News KXXV, used for inspiration only. KXXV and the food bank were not involved."),
        clip_segment(1, "A world model rebuilds it in 3D", "World Labs Marble turns eight frames into Gaussian splats",
                     [(clean / "marble_splat_racking.mp4", 4.5, 0.7)],
                     "Racking aisle: Marble camera poses and metric depth from video frames, fused into splats."),
        clip_segment(2, "Rendered in NVIDIA Omniverse", "The facility is modeled in Blender and shared as OpenUSD",
                     [(clean / "omniverse_rtx_forklift_chase.mp4", 3.6), (clean / "omniverse_rtx_aisle_forklift.mp4", 3.6)],
                     "NVIDIA Omniverse RTX render of the Blender-modeled facility, run on an AWS GPU.",
                     ("Omniverse RTX",)),
        diagram_segment(diagram),
        clip_segment(4, "A live view in the browser", "Cold rooms, doors and forklifts, refreshed from the API",
                     [(clean / "screenrec_overview_tour.mp4", 3.6), (clean / "screenrec_follow_forklift.mp4", 3.6)],
                     "Screen recording of the demo site. Every sensor and inventory value is simulated.",
                     ("Simulated data",)),
        end_segment(),
    ]

    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{S}x{S}",
                            "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", "18",
                            "-pix_fmt", "yuv420p", "-movflags", "+faststart", a.out], stdin=subprocess.PIPE)
    tail = []
    count = 0
    for seg in segments:
        frames = iter(seg)
        if tail:  # crossfade the previous segment's last XF frames into this one's first XF
            for i, prev in enumerate(tail):
                nxt = next(frames)
                w = (i + 1) / (len(tail) + 1)
                enc.stdin.write(Image.blend(prev, nxt, w).tobytes())
                count += 1
        buf = []
        for fr in frames:
            buf.append(fr)
            if len(buf) > XF:
                enc.stdin.write(buf.pop(0).tobytes())
                count += 1
        tail = buf
    for fr in tail:
        enc.stdin.write(fr.tobytes())
        count += 1
    enc.stdin.close()
    enc.wait()
    print(f"{a.out}: {count} frames, {count / FPS:.1f} s")


if __name__ == "__main__":
    main()
