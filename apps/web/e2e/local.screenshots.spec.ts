import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import type { Page } from '@playwright/test';

import { expect, LAUNCH, seedMeeting, test } from './support/fixtures';

// The pictures in the README: `NODO_SCREENSHOTS=1 npx playwright test e2e/local.screenshots.spec.ts`.
// They are taken from the real interface against the local engine with stand-in models.
test.skip(!process.env.NODO_SCREENSHOTS, 'only when the README pictures are regenerated');

const OUT = fileURLToPath(new URL('../../../docs/images/ui/', import.meta.url));
mkdirSync(OUT, { recursive: true });

const shot = (page: Page, name: string, fullPage = false) =>
  page.screenshot({ path: `${OUT}${name}.png`, fullPage });

async function theme(page: Page, value: 'light' | 'dark') {
  await page.addInitScript((choice) => {
    localStorage.setItem('nodo.prefs', JSON.stringify({ locale: 'en', theme: choice, retentionDays: 30 }));
  }, value);
}

test.describe('README pictures', () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test('desktop screens', async ({ page, request }) => {
    await theme(page, 'light');
    const first = await seedMeeting(request, 'Ingestion pipeline planning');
    await seedMeeting(request, 'Retry benchmark review');
    await seedMeeting(request, 'Quarterly roadmap sync');

    await page.goto(LAUNCH);
    await expect(page.getByRole('heading', { name: 'Your meeting memory' })).toBeVisible();
    await page.waitForLoadState('networkidle');
    await shot(page, 'home');

    await page.goto('/meetings/');
    await expect(page.getByRole('heading', { name: 'Ingestion pipeline planning' })).toBeVisible();
    await shot(page, 'meetings');

    await page.goto(`/meeting/?id=${first}`);
    await expect(page.getByText('Sara will benchmark the retries.', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Name speaker' }).first().click();
    await page.getByLabel('Name for speaker S1').fill('Marco Rossi');
    await shot(page, 'meeting');

    await page.goto('/ask/');
    await page.getByLabel('Your question').fill('What did we decide about the ingestion queue?');
    await page.getByRole('button', { name: 'Send question' }).click();
    await expect(page.getByText('The ingestion pipeline moved from synchronous')).toBeVisible();
    await page.waitForLoadState('networkidle');
    await shot(page, 'ask');

    await page.goto('/graph/');
    await expect(page.getByRole('heading', { name: 'Knowledge graph' })).toBeVisible();
    await page.getByRole('radio', { name: 'Map' }).click();
    await expect(page.locator('svg g[role="button"]').first()).toBeVisible();
    await page.setViewportSize({ width: 1280, height: 1080 });
    await shot(page, 'graph');
    await page.setViewportSize({ width: 1280, height: 800 });

    await page.goto('/review/');
    await expect(page.getByRole('button', { name: /^Approve/ }).first()).toBeVisible();
    await shot(page, 'review');

    await page.goto('/capture/');
    await page.getByLabel(/I told the participants/).check();
    await shot(page, 'capture');
    await page.getByRole('button', { name: 'Start recording' }).click();
    await expect(page.getByRole('timer')).toBeVisible();
    await page.waitForTimeout(3500);
    await shot(page, 'recording');
    await page.getByRole('button', { name: 'Stop and save' }).click();
    await expect(page.getByRole('heading', { name: 'Recording saved' })).toBeVisible();
  });

  test('dark theme and settings', async ({ page }) => {
    await theme(page, 'dark');
    await page.goto(LAUNCH);
    await expect(page.getByRole('heading', { name: 'Your meeting memory' })).toBeVisible();
    await page.waitForLoadState('networkidle');
    await page.goto('/settings/');
    await expect(page.getByText('Backend: local')).toBeVisible();
    await shot(page, 'settings-dark', true);
  });

  test('phone width', async ({ page }) => {
    await theme(page, 'light');
    await page.setViewportSize({ width: 390, height: 800 });
    await page.goto(LAUNCH);
    await expect(page.getByRole('heading', { name: 'Your meeting memory' })).toBeVisible();
    await page.waitForLoadState('networkidle');
    await shot(page, 'phone-home');
    await page.goto('/meetings/');
    await expect(page.getByRole('heading', { name: 'Ingestion pipeline planning' })).toBeVisible();
    await shot(page, 'phone-meetings');
  });

  test('Italian', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('nodo.prefs', JSON.stringify({ locale: 'it', theme: 'light', retentionDays: 30 }));
    });
    await page.goto(LAUNCH);
    await expect(page.getByRole('heading', { name: 'La memoria dei tuoi meeting' })).toBeVisible();
    await page.waitForLoadState('networkidle');
    await shot(page, 'home-it');
  });
});
