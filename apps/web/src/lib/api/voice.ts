import { z } from 'zod';

import type { VoiceSession } from '@/features/capture/voiceClient';
import type { Locale } from '../i18n';
import { ApiError, type ApiClient } from './client';

const sessionOutput = z.object({
  session_id: z.string().min(1),
  ticket: z.string().min(20),
  protocol_version: z.number().int().positive(),
  stream_path: z.string().startsWith('/'),
  heartbeat_seconds: z.number().positive(),
  reconnect_grace_seconds: z.number().positive(),
});
const ticketOutput = z.object({ ticket: z.string().min(20) });

export interface VoiceApi {
  openMeeting(meetingId: string, locale: Locale): Promise<VoiceSession>;
  openDictation(locale: Locale): Promise<VoiceSession>;
  openSynthesis(locale: Locale): Promise<VoiceSession>;
  refreshTicket(sessionId: string): Promise<string>;
}

function parse<T>(schema: z.ZodType<T>, value: unknown): T {
  const parsed = schema.safeParse(value);
  if (!parsed.success) throw new ApiError('Unexpected voice API response', 502, 'invalid_response');
  return parsed.data;
}

function streamUrl(baseUrl: string, path: string): string {
  const url = new URL(path, baseUrl);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return url.toString();
}

export function createVoiceApi(client: ApiClient, baseUrl: string): VoiceApi {
  const open = async (mode: 'meeting' | 'dictation' | 'synthesis', locale: Locale, meetingId?: string) => {
    const result = parse(
      sessionOutput,
      await client.post('/v1/voice/sessions', {
        mode,
        locale,
        ...(meetingId ? { meeting_id: meetingId } : {}),
      }),
    );
    return {
      sessionId: result.session_id,
      ticket: result.ticket,
      protocolVersion: result.protocol_version,
      streamUrl: streamUrl(baseUrl, result.stream_path),
      heartbeatSeconds: result.heartbeat_seconds,
      reconnectGraceSeconds: result.reconnect_grace_seconds,
    };
  };
  return {
    async openMeeting(meetingId, locale) {
      return open('meeting', locale, meetingId);
    },
    async openDictation(locale) {
      return open('dictation', locale);
    },
    async openSynthesis(locale) {
      return open('synthesis', locale);
    },

    async refreshTicket(sessionId) {
      const result = parse(
        ticketOutput,
        await client.post(`/v1/voice/sessions/${encodeURIComponent(sessionId)}/tickets`, {}),
      );
      return result.ticket;
    },
  };
}
