// Records the demo video: a scripted tour of the site rendered frame by frame through the
// viewer's ?record=1 mode, so a GPU-less machine still produces smooth footage. Without a GPU
// (software WebGL) a close-up frame takes ~15-30 s, so render a few frames per second and let
// ffmpeg interpolate; on a machine with a GPU use --fps 30 directly.
//
//   npx playwright@1.56.1 --version            # any recent Playwright with Chromium
//   (cd public && python3 -m http.server 8000) &
//   node scripts/record_demo.mjs --url http://localhost:8000/ --out build/demo   # writes PNG frames
//   ffmpeg -framerate 30 -i build/demo/f%05d.png -c:v libx264 -pix_fmt yuv420p -crf 18 demo.mp4
//   # low-fps render (--fps 3): -framerate 3 ... -vf "minterpolate=fps=30:mi_mode=mci" instead
//
// Flags: --width/--height (1920x1080), --fps (30), --settle N (real frames Spark gets to re-sort
// splats before each captured frame, default 1), --from/--to seconds to render a slice, --vendor
// DIR to serve three and Spark from a local node_modules instead of jsDelivr.
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";

const arg = (name, dflt) => { const i = process.argv.indexOf(`--${name}`); return i > 0 ? process.argv[i + 1] : dflt; };
const URL_ = arg("url", "http://localhost:8000/"), OUT = arg("out", "build/demo");
const W = +arg("width", 1920), H = +arg("height", 1080), FPS = +arg("fps", 30), SETTLE = +arg("settle", 1);
const require = createRequire(import.meta.url);
const { chromium } = require(arg("playwright", "playwright"));

// [start s, action]. Views are the buttons on the page; captions are lower thirds.
const SHOTS = [
  [0, { view: "overview", tour: true, caption: ["From video to digital twin", "A food bank warehouse, rebuilt from a walkthrough video"] }],
  [11, { view: "capture-0", tour: false, caption: ["Captured with a world model", "Racking aisle, posed and fused from 8 video frames"] }],
  [21, { view: "capture-1", caption: ["Captured with a world model", "Volunteer sorting room"] }],
  [30, { view: "capture-2", caption: ["Captured with a world model", "Client grocery market"] }],
  [39, { view: "cold", caption: ["Live data on AWS", "Freezer and cooler temperatures, humidity and door state"] }],
  [50, { view: "follow", caption: ["Live data on AWS", "Forklift position, speed and battery"] }],
  [62, { view: "overview", tour: true, caption: ["One OpenUSD twin", "The same scene opens in NVIDIA Omniverse for full-fidelity rendering"] }],
  [74, { caption: null }],
  [76, { end: true }],
];
const END = SHOTS.at(-1)[0];
const from = +arg("from", 0), to = +arg("to", END);

const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const page = await (await browser.newContext({ viewport: { width: W, height: H } })).newPage();
const vendor = arg("vendor");
if (vendor) {
  await page.route("https://cdn.jsdelivr.net/npm/**", (route) => {
    const m = new URL(route.request().url()).pathname.replace("/npm/", "").match(/^(@[^/]+\/[^@/]+|[^@/]+)@[^/]+\/(.*)$/);
    route.fulfill({ path: path.join(vendor, m[1], m[2]), contentType: "application/javascript" });
  });
}
page.on("pageerror", (e) => console.error("page error:", e.message));
await page.goto(`${URL_}${URL_.includes("?") ? "&" : "?"}record=1&overview`);
await page.waitForSelector("#loading.done", { timeout: 600000 });
await page.addStyleTag({ content: `
  #cap { position: fixed; left: 48px; bottom: 120px; max-width: 60%; color: #fff; transition: opacity .6s; opacity: 0; font-family: inherit; }
  #cap.on { opacity: 1; } #cap b { display: block; font-size: 40px; font-weight: 650; letter-spacing: -.01em; text-shadow: 0 2px 18px rgba(0,0,0,.6); }
  #cap span { display: block; margin-top: 6px; font-size: 22px; color: #d6e2ea; text-shadow: 0 2px 12px rgba(0,0,0,.6); }
  #views { opacity: .9; }` });
await page.evaluate(() => { const d = document.createElement("div"); d.id = "cap"; document.body.append(d); });

async function act(a) {
  await page.evaluate((a) => {
    if (a.view) document.querySelector(`#views [data-view="${a.view}"]`)?.click();
    if (a.tour !== undefined) { const t = document.getElementById("tour"); if ((t.getAttribute("aria-pressed") === "true") !== a.tour) t.click(); }
    if (a.caption !== undefined) {
      const c = document.getElementById("cap");
      c.classList.toggle("on", !!a.caption);
      if (a.caption) c.innerHTML = `<b>${a.caption[0]}</b><span>${a.caption[1]}</span>`;
    }
  }, a);
}

fs.mkdirSync(OUT, { recursive: true });
const total = Math.round(END * FPS), t0 = Date.now();
let shot = 0;
for (let f = 0; f < total; f++) {
  const t = f / FPS;
  while (shot < SHOTS.length && SHOTS[shot][0] <= t) await act(SHOTS[shot++][1]);
  await page.evaluate(([ms, settle]) => window.twinStep(ms, settle), [1000 / FPS, SETTLE]);
  if (t < from || t >= to) continue;
  await page.screenshot({ path: path.join(OUT, `f${String(f).padStart(5, "0")}.png`), timeout: 600000 });
  if (f % FPS === 0) console.log(`t=${t.toFixed(1)}s  ${((Date.now() - t0) / 1000).toFixed(0)}s elapsed`);
}
await browser.close();
