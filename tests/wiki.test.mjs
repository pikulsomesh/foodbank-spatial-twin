// The docs wiki (public/wiki/) is static HTML. Every local link and image must resolve, every
// page must be in the side navigation, and the cost page must keep the directional caveat.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const dir = fileURLToPath(new URL("../public/wiki/", import.meta.url));
const pages = readdirSync(dir).filter((f) => f.endsWith(".html"));
const read = (f) => readFileSync(path.join(dir, f), "utf8");

test("every wiki page is in every page's navigation", () => {
  for (const f of pages) {
    const nav = read(f).match(/<nav class="side"[\s\S]*?<\/nav>/)[0];
    for (const g of pages) assert.ok(nav.includes(`href="${g}"`), `${f} nav lacks ${g}`);
    assert.equal((nav.match(/aria-current="page"/g) || []).length, 1, f);
  }
});

test("local links and images resolve", () => {
  for (const f of pages) {
    for (const [, url] of read(f).matchAll(/(?:href|src)="([^"]+)"/g)) {
      if (/^(https?:|data:|mailto:|#)/.test(url)) continue;
      const target = path.resolve(dir, url.split(/[?#]/)[0] || ".");
      const ok = existsSync(target) && (!target.endsWith("/") || existsSync(path.join(target, "index.html")));
      assert.ok(ok || existsSync(path.join(target, "index.html")), `${f}: ${url}`);
    }
  }
});

test("cost page states that figures are directional", () => {
  const html = read("cost.html");
  assert.match(html, /directional/i);
  assert.match(html, /individual AWS account/);
});
