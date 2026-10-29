// Export a .drawio file to PNG with draw.io's own viewer (no desktop app needed).
//   node tools/diagrams/render_drawio.mjs docs/images/azure-architecture-detailed.drawio docs/images/azure-architecture-detailed.png
// Uses Playwright from apps/web (Microsoft Edge) and needs network access to viewer.diagrams.net,
// which also serves the Azure icons. The image is 2x the page size of the diagram.
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../../apps/web/package.json', import.meta.url));
const { chromium } = require('playwright');

const [, , input, output] = process.argv;
if (!input || !output) {
  console.error('usage: node render_drawio.mjs in.drawio out.png');
  process.exit(2);
}
const source = readFileSync(input, 'utf8');
const width = Number(/pageWidth="(\d+)"/.exec(source)?.[1] ?? 1800);
const height = Number(/pageHeight="(\d+)"/.exec(source)?.[1] ?? 1000);
const xml = source.replaceAll('image=img/lib/', 'image=https://app.diagrams.net/img/lib/');
const config = JSON.stringify({ xml });
// No doctype: the viewer lays the graph out differently in standards mode.
const html = `<body style="margin:0;background:#fff">
<div class="mxgraph" data-mxgraph='${config.replaceAll('&', '&amp;').replaceAll("'", '&#39;')}'></div>
<script src="https://viewer.diagrams.net/js/viewer-static.min.js"></script></body>`;

const browser = await chromium.launch({ channel: process.env.PW_CHANNEL ?? 'msedge' });
const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 2 });
await page.setContent(html, { waitUntil: 'load' });
await page.waitForSelector('svg', { timeout: 30000, state: 'attached' });
await page.waitForLoadState('networkidle');
await page.waitForTimeout(1500);
await page.screenshot({ path: output, clip: { x: 0, y: 0, width, height } });
await browser.close();
console.log(`${output}: ${width * 2}x${height * 2}`);
