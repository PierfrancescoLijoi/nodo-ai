import { expect, test as base, type APIRequestContext } from '@playwright/test';

export const TOKEN = 'e2e-local-engine-key-0123456789-0123456789';
/** The address the desktop launcher opens: the key travels in the fragment, which no server receives. */
export const LAUNCH = `/#token=${TOKEN}`;

const ATTESTATION = {
  notice_version: '2026-10-v1',
  locale: 'en',
  retention_policy_id: 'retention-30d',
  acknowledged: true,
};

/** Records a meeting through the real API (fake audio, stand-in models) and waits until it is in review. */
export async function seedMeeting(request: APIRequestContext, title: string): Promise<string> {
  const auth = { Authorization: `Bearer ${TOKEN}` };
  const created = await request.post('/v1/meetings', {
    headers: { ...auth, 'Idempotency-Key': `seed-${crypto.randomUUID()}` },
    data: { recording_attestation: ATTESTATION, title },
  });
  expect(created.ok()).toBeTruthy();
  const { id } = (await created.json()) as { id: string };
  const session = (await (
    await request.post(`/v1/meetings/${id}/upload-sessions`, { headers: auth, data: {} })
  ).json()) as {
    upload_url: string;
  };
  expect((await request.put(session.upload_url, { data: Buffer.from('fake audio') })).status()).toBe(204);
  expect((await request.post(`/v1/meetings/${id}/complete`, { headers: auth })).ok()).toBeTruthy();
  await expect
    .poll(
      async () =>
        ((await (await request.get(`/v1/meetings/${id}`, { headers: auth })).json()) as { state: string })
          .state,
      {
        timeout: 20_000,
      },
    )
    .toBe('review');
  return id;
}

/**
 * Every test fails on a content policy violation or an uncaught page error: the policy is part of the
 * product, and a script or style it blocks must not go unnoticed.
 */
export const test = base.extend<{ guard: void }>({
  guard: [
    async ({ page }, use) => {
      const problems: string[] = [];
      page.on('console', (message) => {
        if (/content security policy/i.test(message.text())) problems.push(message.text());
      });
      page.on('pageerror', (error) => problems.push(`page error: ${error.message}`));
      await page.addInitScript(() => {
        // English and the light theme, so the assertions do not depend on the machine running them.
        if (!localStorage.getItem('nodo.prefs')) {
          localStorage.setItem(
            'nodo.prefs',
            JSON.stringify({ locale: 'en', theme: 'light', retentionDays: 30 }),
          );
        }
      });
      await use();
      expect(problems).toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };
