import { expect, LAUNCH, test } from './support/fixtures';

// "Copy to Azure" from the desktop app: the page opens the identity provider in a pop-up, the pop-up's
// callback page finishes the code exchange, and the access token reaches the settings page over a
// same-origin channel. The provider here is the Azure stand-in of the cloud project (another origin, so the
// token request is cross-origin as it is with Entra for a single-page-application redirect URI).
test('signs in to Azure in a pop-up and hands the token to the settings page', async ({ page, context }) => {
  await page.goto(LAUNCH);
  await page.getByRole('link', { name: 'Settings' }).first().click();

  await page.getByLabel('API address').fill('http://127.0.0.1:8796');
  await page.getByLabel('Issuer address').fill('http://127.0.0.1:8796/idp');
  await page.getByLabel('Client ID').fill('e2e-client');
  await page.getByLabel('API scope').fill('api://nodo/access');

  const popup = context.waitForEvent('page');
  await page.getByRole('button', { name: 'Sign in to Azure' }).click();
  await popup;

  await expect(page.getByText(/Ready to copy/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole('button', { name: 'Copy now' })).toBeEnabled();
});

test('the Azure sign-in needs a complete, safe target', async ({ page }) => {
  await page.goto(LAUNCH);
  await page.getByRole('link', { name: 'Settings' }).first().click();
  await page.getByLabel('API address').fill('http://insecure.example');
  await page.getByLabel('Issuer address').fill('https://login.example/t/v2.0');
  await page.getByLabel('Client ID').fill('c');
  await page.getByLabel('API scope').fill('s');
  await expect(page.getByRole('button', { name: 'Sign in to Azure' })).toBeDisabled();
  await expect(page.getByText(/complete HTTPS addresses/)).toBeVisible();
});
