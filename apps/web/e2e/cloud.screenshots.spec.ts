import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { expect, test } from './support/fixtures';

// README picture of the Azure sign-in: `NODO_SCREENSHOTS=1 npx playwright test e2e/cloud.screenshots.spec.ts`.
test.skip(!process.env.NODO_SCREENSHOTS, 'only when the README pictures are regenerated');

const OUT = fileURLToPath(new URL('../../../docs/images/ui/', import.meta.url));
mkdirSync(OUT, { recursive: true });

test('Azure sign-in screen', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Sign in to NODO' })).toBeVisible();
  await page.screenshot({ path: `${OUT}azure-sign-in.png` });
});
