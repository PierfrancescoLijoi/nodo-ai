import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from './client';
import { createVoiceApi } from './voice';

describe('createVoiceApi', () => {
  it('opens a meeting session and turns the HTTP API origin into a WebSocket URL', async () => {
    const post = vi.fn().mockResolvedValue({
      session_id: 's1',
      ticket: 'ticket-ticket-ticket-ticket',
      protocol_version: 1,
      stream_path: '/v1/voice/stream',
      heartbeat_seconds: 15,
      reconnect_grace_seconds: 30,
    });
    const api = createVoiceApi({ post } as unknown as ApiClient, 'https://api.test/base');

    const session = await api.openMeeting('m/1', 'it');

    expect(post).toHaveBeenCalledWith('/v1/voice/sessions', {
      mode: 'meeting',
      locale: 'it',
      meeting_id: 'm/1',
    });
    expect(session).toMatchObject({ sessionId: 's1', streamUrl: 'wss://api.test/v1/voice/stream' });
  });

  it('asks for a one-use reconnect ticket', async () => {
    const post = vi.fn().mockResolvedValue({ ticket: 'fresh-ticket-ticket-ticket' });
    const api = createVoiceApi({ post } as unknown as ApiClient, 'http://localhost:8080');

    await expect(api.refreshTicket('s/1')).resolves.toBe('fresh-ticket-ticket-ticket');
    expect(post).toHaveBeenCalledWith('/v1/voice/sessions/s%2F1/tickets', {});
  });
});
