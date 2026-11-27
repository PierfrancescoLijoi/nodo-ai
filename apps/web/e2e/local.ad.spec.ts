import { mkdirSync, writeFileSync } from 'node:fs';

import type { Locator, Page } from '@playwright/test';

import { expect, LAUNCH, seedMeeting, test } from './support/fixtures';

// Raw screen footage for the NODO ad: `NODO_AD=1 npx playwright test e2e/local.ad.spec.ts --project=local`.
// Real interface, local engine with stand-in models and sample data. No captions: the ad adds its own.
// Section start times (seconds into the video) are written to NODO_AD_OUT/stamps.json.
test.skip(!process.env.NODO_AD, 'only when the ad footage is recorded');

const OUT = process.env.NODO_AD_OUT ?? 'test-results/ad';

test.use({
  viewport: { width: 1280, height: 800 },
  video: { mode: 'on', size: { width: 1280, height: 800 } },
});

async function cursor(page: Page) {
  await page.addInitScript(() => {
    const install = () => {
      if (document.getElementById('demo-cursor')) return;
      const c = document.createElement('div');
      c.id = 'demo-cursor';
      const s = c.style;
      s.position = 'fixed';
      s.left = '0px';
      s.top = '0px';
      s.width = '34px';
      s.height = '34px';
      s.zIndex = '2147483647';
      s.pointerEvents = 'none';
      s.transition = 'transform 40ms linear';
      c.innerHTML =
        '<svg xmlns="http://www.w3.org/2000/svg" width="34" height="34" viewBox="0 0 24 24"><path d="M3 2l17 8-7 2-3 7z" fill="#0b1526" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
      document.body.appendChild(c);
      window.addEventListener('mousemove', (e) => {
        c.style.transform = `translate(${e.clientX - 3}px, ${e.clientY - 2}px)`;
      });
      window.addEventListener('mousedown', (e) => {
        const r = document.createElement('div');
        const rs = r.style;
        rs.position = 'fixed';
        rs.left = `${e.clientX - 28}px`;
        rs.top = `${e.clientY - 28}px`;
        rs.width = '56px';
        rs.height = '56px';
        rs.borderRadius = '50%';
        rs.border = '4px solid #4fd1c0';
        rs.zIndex = '2147483646';
        rs.pointerEvents = 'none';
        rs.transition = 'transform 450ms ease-out, opacity 450ms ease-out';
        document.body.appendChild(r);
        requestAnimationFrame(() => {
          rs.transform = 'scale(1.8)';
          rs.opacity = '0';
        });
        setTimeout(() => r.remove(), 600);
      });
    };
    if (document.body) install();
    else document.addEventListener('DOMContentLoaded', install);
  });
}

async function glide(page: Page, target: Locator) {
  const box = await target.boundingBox();
  if (!box) throw new Error('no box');
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 28 });
  await page.waitForTimeout(250);
}

async function click(page: Page, target: Locator) {
  await glide(page, target);
  await page.mouse.down();
  await page.mouse.up();
}

test('ad footage', async ({ page, request }) => {
  test.setTimeout(240_000);
  mkdirSync(OUT, { recursive: true });
  await cursor(page);
  await page.addInitScript(() => {
    localStorage.setItem('nodo.prefs', JSON.stringify({ locale: 'en', theme: 'light', retentionDays: 30 }));
  });
  const first = await seedMeeting(request, 'Ingestion pipeline planning');
  await seedMeeting(request, 'Retry benchmark review');
  await seedMeeting(request, 'Quarterly roadmap sync');

  const t0 = Date.now();
  const stamps: Record<string, number> = {};
  const mark = (name: string) => {
    stamps[name] = Number(((Date.now() - t0) / 1000).toFixed(2));
  };

  await page.goto(LAUNCH);
  await expect(page.getByRole('heading', { name: 'Your meeting memory' })).toBeVisible();
  await page.waitForLoadState('networkidle');
  mark('home');
  await page.mouse.move(640, 500, { steps: 20 });
  await page.waitForTimeout(4500);

  await page.goto('/capture/');
  mark('capture');
  await page.waitForTimeout(1200);
  await click(page, page.getByLabel(/I told the participants/));
  await page.getByLabel(/I told the participants/).check();
  await click(page, page.getByRole('button', { name: 'Start recording' }));
  await expect(page.getByRole('timer')).toBeVisible();
  await page.waitForTimeout(5000);
  await click(page, page.getByRole('button', { name: 'Stop and save' }));
  await expect(page.getByRole('heading', { name: 'Recording saved' })).toBeVisible();
  await page.waitForTimeout(1800);

  await page.goto(`/meeting/?id=${first}`);
  await expect(page.getByText('Sara will benchmark the retries.', { exact: true })).toBeVisible();
  mark('meeting');
  await page.waitForTimeout(2200);
  await click(page, page.getByRole('button', { name: 'Name speaker' }).first());
  await page.getByLabel('Name for speaker S1').pressSequentially('Marco Rossi', { delay: 110 });
  await page.waitForTimeout(3200);

  await page.goto('/ask/');
  mark('ask');
  await page.waitForTimeout(900);
  await click(page, page.getByLabel('Your question'));
  await page.getByLabel('Your question').pressSequentially('What did we decide about the ingestion queue?', {
    delay: 60,
  });
  await click(page, page.getByRole('button', { name: 'Send question' }));
  await expect(page.getByText('The ingestion pipeline moved from synchronous')).toBeVisible();
  await page.waitForTimeout(5000);

  await page.goto('/graph/');
  await expect(page.getByRole('heading', { name: 'Knowledge graph' })).toBeVisible();
  mark('graph');
  await page.waitForTimeout(800);
  await click(page, page.getByRole('radio', { name: 'Map' }));
  await expect(page.locator('svg g[role="button"]').first()).toBeVisible();
  await page.waitForTimeout(4500);

  await page.goto('/brain/');
  mark('brain');
  await page.waitForTimeout(5500);

  await page.goto('/review/');
  await expect(page.getByRole('button', { name: /^Approve/ }).first()).toBeVisible();
  mark('review');
  await page.waitForTimeout(1200);
  await click(page, page.getByRole('button', { name: /^Approve/ }).first());
  await page.waitForTimeout(3500);

  await page.goto('/models/');
  mark('models');
  await page.mouse.move(700, 400, { steps: 20 });
  await page.waitForTimeout(4500);

  await page.goto('/settings/');
  await expect(page.getByText('Backend: local')).toBeVisible();
  mark('settings');
  await page.waitForTimeout(4500);
  mark('end');
  writeFileSync(`${OUT}/stamps.json`, JSON.stringify(stamps, null, 2));
});
