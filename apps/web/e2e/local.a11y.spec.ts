import AxeBuilder from '@axe-core/playwright';

import { expect, LAUNCH, seedMeeting, test } from './support/fixtures';

const PAGES = [
  { name: 'home', path: '/' },
  { name: 'meetings', path: '/meetings/' },
  { name: 'ask', path: '/ask/' },
  { name: 'graph', path: '/graph/' },
  { name: 'review', path: '/review/' },
  { name: 'capture', path: '/capture/' },
  { name: 'settings', path: '/settings/' },
];

for (const theme of ['light', 'dark'] as const) {
  test.describe(`accessibility, ${theme} theme`, () => {
    test.beforeEach(async ({ page }) => {
      await page.addInitScript((value) => {
        localStorage.setItem('nodo.prefs', JSON.stringify({ locale: 'en', theme: value, retentionDays: 30 }));
      }, theme);
    });

    test('every screen has no detectable violations', async ({ page, request }) => {
      const id = await seedMeeting(request, 'Accessibility check');
      await page.goto(LAUNCH);
      await expect(page.getByRole('heading', { name: 'Your meeting memory' })).toBeVisible();
      for (const target of [...PAGES, { name: 'meeting', path: `/meeting/?id=${id}` }]) {
        await page.goto(target.path);
        await expect(page.locator('main h1')).toBeVisible();
        await page.waitForLoadState('networkidle');
        const result = await new AxeBuilder({ page })
          .withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'])
          .analyze();
        expect(
          result.violations.map((violation) => `${target.name}: ${violation.id} (${violation.nodes.length})`),
        ).toEqual([]);
      }
    });
  });
}

test('the page can be used from the keyboard: skip link, then the first control', async ({ page }) => {
  await page.goto(LAUNCH);
  await expect(page.getByRole('heading', { name: 'Your meeting memory' })).toBeVisible();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#main')).toBeFocused();
});

test('works at phone width without sideways scrolling', async ({ page, request }) => {
  await seedMeeting(request, 'Phone width');
  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto(LAUNCH);
  for (const path of ['/', '/meetings/', '/graph/', '/settings/', '/capture/']) {
    await page.goto(path);
    await expect(page.locator('main h1')).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, path).toBeLessThanOrEqual(0);
  }
  await expect(page.getByRole('navigation', { name: 'Main navigation' }).last()).toBeVisible();
});
