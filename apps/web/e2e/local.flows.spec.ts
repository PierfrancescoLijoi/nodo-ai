import { expect, LAUNCH, seedMeeting, test } from './support/fixtures';

test.describe('local engine', () => {
  test('opens with the launcher key and removes it from the address', async ({ page }) => {
    await page.goto(LAUNCH);
    await expect(page.getByRole('heading', { name: 'Your meeting memory' })).toBeVisible();
    expect(page.url()).not.toContain('token');
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Your meeting memory' })).toBeVisible();
  });

  test('a page opened without the key says NODO is not connected', async ({ browser }) => {
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'NODO is not connected' })).toBeVisible();
    await context.close();
  });

  test('lists meetings and filters them by what you type', async ({ page, request }) => {
    await seedMeeting(request, 'Roadmap review');
    await seedMeeting(request, 'Hiring sync');
    await page.goto(LAUNCH);
    await page.getByRole('link', { name: 'Meetings' }).first().click();
    await expect(page.getByRole('heading', { name: 'Roadmap review' })).toBeVisible();
    await page.getByLabel('Search meetings').fill('hiring');
    await expect(page.getByRole('heading', { name: 'Hiring sync' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Roadmap review' })).toHaveCount(0);
  });

  test('opens a meeting, names a speaker and deletes the meeting', async ({ page, request }) => {
    const title = `Naming ${Date.now()}`;
    await seedMeeting(request, title);
    await page.goto(LAUNCH);
    await page.getByRole('link', { name: 'Meetings' }).first().click();
    await page.getByRole('heading', { name: title }).click();

    await expect(page.getByRole('heading', { name: title, level: 1 })).toBeVisible();
    await expect(page.getByText('We start with the ingestion queue.', { exact: true })).toBeVisible();

    await page.getByRole('button', { name: 'Name speaker' }).first().click();
    const input = page.getByLabel('Name for speaker S1');
    await input.fill('Marco Rossi');
    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByRole('heading', { name: 'Marco Rossi', level: 3 })).toBeVisible();

    await page.getByRole('button', { name: 'Name speaker' }).first().click();
    await page.getByLabel('Name for speaker S2').fill('bad <name>');
    await expect(page.getByText(/Use only letters/)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Save' })).toBeDisabled();

    await page.getByRole('button', { name: 'Delete meeting' }).click();
    await page.getByRole('button', { name: 'Yes, delete meeting' }).click();
    await expect(page).toHaveURL(/\/meetings\/$/);
    await expect(page.getByRole('heading', { name: title })).toHaveCount(0);
  });

  test('answers a question with the sentences it comes from', async ({ page, request }) => {
    await seedMeeting(request, 'Queue talk');
    await page.goto(LAUNCH);
    await page.getByRole('link', { name: 'Ask' }).first().click();
    await page.getByLabel('Your question').fill('what did we decide about the ingestion queue');
    await page.getByRole('button', { name: 'Send question' }).click();
    await expect(
      page.getByText('The ingestion pipeline moved from synchronous processing to an async queue.'),
    ).toBeVisible();
    const source = page.getByRole('link', { name: /We start with the ingestion queue/ }).first();
    await expect(source).toBeVisible();
    await source.click();
    await expect(page.getByText('Cited in an answer')).toBeVisible();
  });

  test('shows the graph as a list and as a map, and reviews a proposal', async ({ page, request }) => {
    await seedMeeting(request, 'Graph talk');
    await page.goto(LAUNCH);
    await page.getByRole('link', { name: 'Graph' }).first().click();
    await expect(page.getByRole('heading', { name: 'Knowledge graph' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Sara' }).first()).toBeVisible();

    await page.getByRole('radio', { name: 'Map' }).click();
    const nodes = page.locator('svg g[role="button"]');
    await expect(nodes.nth(1)).toBeVisible();
    const label = ((await nodes.nth(1).getAttribute('aria-label')) ?? '').split(', ')[0] ?? '';
    await nodes.nth(1).focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { name: label, level: 3 })).toBeVisible();

    await page.getByRole('link', { name: 'Review proposals' }).click();
    const waiting = page
      .getByRole('listitem')
      .filter({ has: page.getByRole('button', { name: /^Approve/ }) });
    await expect(waiting.first()).toBeVisible(); // the inbox loads after the page does
    const before = await waiting.count();
    expect(before).toBeGreaterThan(0);
    await waiting
      .first()
      .getByRole('button', { name: /^Approve/ })
      .click();
    await expect(waiting).toHaveCount(before - 1);
  });

  test('records with a microphone, uploads, and opens the meeting when review is ready', async ({ page }) => {
    await page.goto(LAUNCH);
    await page.getByRole('link', { name: 'New recording' }).click();
    const start = page.getByRole('button', { name: 'Start recording' });
    await expect(start).toBeDisabled();
    await page.getByLabel(/I told the participants/).check();
    await expect(start).toBeEnabled();
    await start.click();

    await expect(page.getByRole('timer')).toBeVisible();
    await page.waitForTimeout(2200);
    await page.getByRole('button', { name: 'Pause' }).click();
    await expect(page.getByText('Paused', { exact: true }).first()).toBeVisible();
    await page.getByRole('button', { name: 'Resume' }).click();
    await page.getByRole('button', { name: 'Stop and save' }).click();

    await expect(page.getByRole('heading', { name: 'Recording saved' })).toBeVisible();
    const open = page.getByRole('button', { name: 'Open meeting' });
    await expect(open).toBeVisible({ timeout: 30_000 });
    await open.click();
    await expect(page.getByText('Sara will benchmark the retries.', { exact: true })).toBeVisible();
  });

  test('streams PCM live, renders a final transcript and acknowledges it', async ({ page }) => {
    let binaryFrames = 0;
    let socketConnections = 0;
    let refreshedTickets = 0;
    const acknowledgements: number[] = [];
    await page.route('**/v1/voice/sessions', async (route) => {
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: 'e2e-live-session',
          ticket: 'e2e-live-ticket-01234567890123456789',
          ticket_expires_in: 60,
          protocol_version: 1,
          stream_path: '/v1/voice/stream',
          heartbeat_seconds: 15,
          reconnect_grace_seconds: 30,
        }),
      });
    });
    await page.route('**/v1/voice/sessions/*/tickets', async (route) => {
      refreshedTickets += 1;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ticket: 'e2e-fresh-ticket-01234567890123456789' }),
      });
    });
    await page.routeWebSocket('**/v1/voice/stream', (socket) => {
      socketConnections += 1;
      const connection = socketConnections;
      socket.onMessage((message) => {
        if (Buffer.isBuffer(message)) {
          binaryFrames += 1;
          if (binaryFrames === 1) {
            socket.send(
              JSON.stringify({
                type: 'transcript.final',
                sequence: 1,
                text: 'Live transcript reached the screen.',
                audio_offset_end_ms: 40,
              }),
            );
          }
          return;
        }
        const control = JSON.parse(message);
        if (control.type === 'hello') {
          socket.send(
            JSON.stringify({
              type: 'session.ready',
              audio_resume_offset_ms: connection === 1 ? 0 : 40,
            }),
          );
        }
        if (control.type === 'ack') {
          acknowledgements.push(control.sequence);
          if (connection === 1) void socket.close({ code: 1012, reason: 'rolling restart' });
        }
        if (control.type === 'end') {
          socket.send(JSON.stringify({ type: 'session.closed', sequence: 2 }));
        }
      });
    });

    await page.goto(LAUNCH);
    await page.getByRole('link', { name: 'New recording' }).click();
    await page.getByLabel(/I told the participants/).check();
    await page.getByRole('button', { name: 'Start recording' }).click();

    await expect(page.getByRole('timer')).toBeVisible();
    await expect(page.getByText('Live transcript reached the screen.')).toBeVisible({
      timeout: 10_000,
    });
    expect(binaryFrames).toBeGreaterThan(0);
    expect(acknowledgements).toContain(1);
    await expect.poll(() => socketConnections).toBe(2);
    expect(refreshedTickets).toBe(1);
    await page.getByRole('button', { name: 'Stop and save' }).click();
    await expect(page.getByRole('heading', { name: 'Recording saved' })).toBeVisible();
  });

  test('settings: engine, index check, language, theme and export', async ({ page, request }) => {
    await seedMeeting(request, 'Settings talk');
    await page.goto(LAUNCH);
    await page.getByRole('link', { name: 'Settings' }).first().click();

    await expect(page.getByText('Backend: local')).toBeVisible();
    await page.getByRole('button', { name: 'Check' }).click();
    await expect(page.getByText('Everything matches.')).toBeVisible({ timeout: 15_000 });

    await page.getByRole('radio', { name: 'Italiano' }).click();
    await expect(page.getByRole('heading', { name: 'Privacy e controllo' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.lang)).toBe('it');
    await page.getByRole('radio', { name: 'English' }).click();

    await page.getByRole('radio', { name: 'Dark' }).click();
    expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe('dark');
    await page.reload();
    expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe('dark');
    await page.getByRole('radio', { name: 'Light' }).click();

    await page.getByRole('button', { name: 'Prepare export' }).click();
    const download = page.getByRole('button', { name: 'Download file' });
    await expect(download).toBeVisible({ timeout: 30_000 });
    const saved = page.waitForEvent('download');
    await download.click();
    const file = await saved;
    expect(file.suggestedFilename()).toBe('nodo-export.json');
  });
});
