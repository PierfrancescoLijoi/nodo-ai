import { expect, test } from './support/fixtures';

const state = (request: import('@playwright/test').APIRequestContext) =>
  request.get('/__test/log').then((reply) => reply.json() as Promise<{ path: string; token: string }[]>);

async function signIn(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Your meeting memory' })).toBeVisible();
}

test.describe('Azure deployment (stand-in)', () => {
  test('asks for sign-in, completes the code flow with PKCE and calls the API with the issued token', async ({
    page,
    request,
  }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Sign in to NODO' })).toBeVisible();
    await signIn(page);

    await expect(page.getByText('Cloud planning').first()).toBeVisible();
    expect(page.url()).not.toMatch(/code=|state=/);
    const calls = await state(request);
    const used = calls.filter((call) => call.path === '/v1/meetings').map((call) => call.token);
    expect(used.length).toBeGreaterThan(0);
    expect(used.every((token) => token.startsWith('at-'))).toBe(true);

    await page.reload();
    await expect(page.getByRole('heading', { name: 'Your meeting memory' })).toBeVisible();
  });

  test('a deep link survives the sign-in', async ({ page }) => {
    await page.goto('/meeting/?id=m-1&seg=s-9');
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page).toHaveURL(/\/meeting\/\?id=m-1&seg=s-9$/);
  });

  test('an address fragment does not turn the deployment into a local page', async ({ browser }) => {
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto('/#token=attacker-supplied-key-0123456789');
    await expect(page.getByRole('heading', { name: 'Sign in to NODO' })).toBeVisible();
    await context.close();
  });

  test('renews an expired access token with the refresh token, without asking again', async ({
    page,
    request,
  }) => {
    await signIn(page);
    await request.get('/__test/expire');
    const before = (await state(request)).length;
    await page.getByRole('link', { name: 'Meetings' }).first().click();
    await page.reload(); // forces fresh data with the lapsed token
    await expect(page.getByRole('heading', { name: 'Meetings' })).toBeVisible();
    await expect(page.getByText('Cloud planning')).toBeVisible();
    const calls = (await state(request)).slice(before);
    expect(calls.some((call) => call.path === '/v1/meetings')).toBe(true);
    expect(calls.filter((call) => call.path === '/v1/meetings').at(-1)?.token).toMatch(/^at-/);
  });

  test('goes back to sign-in when the session can no longer be renewed', async ({ page, request }) => {
    await signIn(page);
    await request.get('/__test/revoke');
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Sign in to NODO' })).toBeVisible();
  });

  test('signs out', async ({ page }) => {
    await signIn(page);
    await page.getByRole('link', { name: 'Settings' }).first().click();
    await page.getByRole('button', { name: 'Sign out' }).click();
    await expect(page.getByRole('heading', { name: 'Sign in to NODO' })).toBeVisible();
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Sign in to NODO' })).toBeVisible();
  });
});
