import type { Page } from '@playwright/test';

import { expect, LAUNCH, seedMeeting, test } from './support/fixtures';

// The LinkedIn demo video: `NODO_DEMO=1 npx playwright test e2e/local.demo.spec.ts --project=local`.
// Real interface, local engine with stand-in models and sample data. Playwright writes the .webm
// under test-results/.
test.skip(!process.env.NODO_DEMO, 'only when the demo video is recorded');

test.use({
  viewport: { width: 1280, height: 720 },
  video: { mode: 'on', size: { width: 1280, height: 720 } },
});

async function caption(page: Page, text: string) {
  await page.evaluate((value) => {
    let box = document.getElementById('demo-caption');
    if (!box) {
      box = document.createElement('div');
      box.id = 'demo-caption';
      const s = box.style;
      s.position = 'fixed';
      s.left = '50%';
      s.bottom = '28px';
      s.transform = 'translateX(-50%)';
      s.zIndex = '2147483647';
      s.maxWidth = '86%';
      s.padding = '12px 22px';
      s.borderRadius = '14px';
      s.background = 'rgba(8,15,29,0.92)';
      s.color = '#e8f1fb';
      s.font = '600 22px "Segoe UI", system-ui, sans-serif';
      s.border = '1px solid #4fd1c0';
      s.pointerEvents = 'none';
      s.textAlign = 'center';
      document.body.appendChild(box);
    }
    box.textContent = value;
  }, text);
}

const dwell = (page: Page, ms = 2600) => page.waitForTimeout(ms);

test('demo video', async ({ page, request }) => {
  test.setTimeout(180_000);
  await page.addInitScript(() => {
    localStorage.setItem('nodo.prefs', JSON.stringify({ locale: 'en', theme: 'light', retentionDays: 30 }));
  });
  const first = await seedMeeting(request, 'Ingestion pipeline planning');
  await seedMeeting(request, 'Retry benchmark review');
  await seedMeeting(request, 'Quarterly roadmap sync');

  await page.goto(LAUNCH);
  await expect(page.getByRole('heading', { name: 'Your meeting memory' })).toBeVisible();
  await page.waitForLoadState('networkidle');
  await caption(page, 'NODO AI: a private memory for your meetings and documents');
  await dwell(page, 3200);

  await page.goto('/capture/');
  await caption(page, 'Recording is always visible and starts only after an attestation');
  await dwell(page, 1800);
  await page.getByLabel(/I told the participants/).check();
  await page.getByRole('button', { name: 'Start recording' }).click();
  await expect(page.getByRole('timer')).toBeVisible();
  await caption(page, 'Live capture, streamed in real time, with an upload fallback');
  await dwell(page, 4000);
  await page.getByRole('button', { name: 'Stop and save' }).click();
  await expect(page.getByRole('heading', { name: 'Recording saved' })).toBeVisible();
  await dwell(page, 1500);

  await page.goto('/meetings/');
  await expect(page.getByRole('heading', { name: 'Ingestion pipeline planning' })).toBeVisible();
  await caption(page, 'Every meeting becomes searchable, speaker-aware text');
  await dwell(page);

  await page.goto(`/meeting/?id=${first}`);
  await expect(page.getByText('Sara will benchmark the retries.', { exact: true })).toBeVisible();
  await caption(page, 'Summary, decisions and actions, each linked to the exact sentence');
  await dwell(page, 3000);
  await page.getByRole('button', { name: 'Name speaker' }).first().click();
  await page.getByLabel('Name for speaker S1').pressSequentially('Marco Rossi', { delay: 90 });
  await caption(page, 'You name the speakers. No biometric voice recognition');
  await dwell(page, 2600);

  await page.goto('/ask/');
  await caption(page, 'Ask anything. Answers come only with citations');
  await page.getByLabel('Your question').pressSequentially('What did we decide about the ingestion queue?', {
    delay: 55,
  });
  await page.getByRole('button', { name: 'Send question' }).click();
  await expect(page.getByText('The ingestion pipeline moved from synchronous')).toBeVisible();
  await dwell(page, 4000);

  await page.goto('/graph/');
  await expect(page.getByRole('heading', { name: 'Knowledge graph' })).toBeVisible();
  await page.getByRole('radio', { name: 'Map' }).click();
  await expect(page.locator('svg g[role="button"]').first()).toBeVisible();
  await caption(page, 'A temporal knowledge graph of people, topics, decisions and actions');
  await dwell(page, 3200);

  await page.goto('/review/');
  await expect(page.getByRole('button', { name: /^Approve/ }).first()).toBeVisible();
  await caption(page, 'Inferences stay proposals until a human approves them');
  await dwell(page, 3200);

  await page.goto('/models/');
  await caption(page, 'Opt-in local models, each verified by checksum');
  await dwell(page, 3200);

  await page.addInitScript(() => {
    localStorage.setItem('nodo.prefs', JSON.stringify({ locale: 'en', theme: 'dark', retentionDays: 30 }));
  });
  await page.goto('/settings/');
  await expect(page.getByText('Backend: local')).toBeVisible();
  await caption(page, 'Backup, restore, full export. English, Polish, Italian, Danish. Light and dark');
  await dwell(page, 3400);
});
