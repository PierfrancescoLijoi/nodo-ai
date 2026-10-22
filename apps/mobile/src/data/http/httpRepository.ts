import type { Locale } from '@/i18n';

import type { MeetingState, MeetingsRepository } from '../types';
import { ApiError, type ApiClient } from './client';
import { mapAnswer, mapChange, mapGraph, mapMeeting, mapMeetingSummary } from './mappers';
import { uploadAudioToUrl } from './upload';

interface Page<T> {
  items: T[];
  next_cursor?: string | null;
}

async function cursorPages(client: ApiClient, path: string, limit: number, maxPages = 4): Promise<unknown[]> {
  const items: unknown[] = [];
  let cursor: string | undefined;
  for (let pageNumber = 0; pageNumber < maxPages; pageNumber += 1) {
    const suffix = cursor ? `&cursor=${encodeURIComponent(cursor)}` : '';
    const page = await client.get<Page<unknown>>(`${path}${path.includes('?') ? '&' : '?'}limit=${limit}${suffix}`);
    items.push(...page.items);
    if (!page.next_cursor) break;
    cursor = page.next_cursor;
  }
  return items;
}

async function transcript(client: ApiClient, id: string): Promise<unknown[]> {
  const items: unknown[] = [];
  for (let offset = 0; ; offset += 200) {
    const page = await client.get<Page<unknown>>(
      `/v1/meetings/${encodeURIComponent(id)}/transcript?limit=200&offset=${offset}`,
    );
    items.push(...page.items);
    if (page.items.length < 200) return items;
  }
}

interface HttpRepositoryOptions {
  mode?: 'local' | 'azure';
  fetchImpl?: typeof fetch;
  createIdempotencyKey?: () => string;
}

const randomKey = () => {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID();
  return `nodo-${Date.now()}-${Math.random().toString(36).slice(2)}`;
};

export function createHttpRepository(
  client: ApiClient,
  locale: Locale,
  { mode = 'azure', fetchImpl = fetch, createIdempotencyKey = randomKey }: HttpRepositoryOptions = {},
): MeetingsRepository {
  return {
    async createMeeting({ title, locale: recordingLocale, retentionDays }) {
      const response = await client.post<{ id: string }>(
        '/v1/meetings',
        {
          recording_attestation: {
            notice_version: '2026-10-v1',
            locale: recordingLocale,
            retention_policy_id: `retention-${retentionDays}d`,
            acknowledged: true,
          },
          ...(title ? { title } : {}),
        },
        { headers: { 'Idempotency-Key': createIdempotencyKey() } },
      );
      return { id: response.id };
    },
    async uploadAudio(meetingId, audio, onProgress) {
      const encoded = encodeURIComponent(meetingId);
      const session = await client.post<{ upload_url: string }>(`/v1/meetings/${encoded}/upload-sessions`, {});
      await uploadAudioToUrl({ url: session.upload_url, audio, mode, fetchImpl, onProgress });
      await client.post(`/v1/meetings/${encoded}/complete`, {});
    },
    async getMeetingState(id) {
      const meeting = await client.get<{ state: MeetingState }>(`/v1/meetings/${encodeURIComponent(id)}`);
      return meeting.state;
    },
    async deleteMeeting(id) {
      await client.delete(`/v1/meetings/${encodeURIComponent(id)}`);
    },
    async listMeetings() {
      return (await cursorPages(client, '/v1/meetings', 50)).map((item) =>
        mapMeetingSummary(item as Record<string, unknown>),
      );
    },
    async getMeeting(id) {
      try {
        const encoded = encodeURIComponent(id);
        const [meeting, segments] = await Promise.all([client.get(`/v1/meetings/${encoded}`), transcript(client, id)]);
        return mapMeeting(meeting as Record<string, unknown>, segments as Record<string, unknown>[]);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return undefined;
        throw error;
      }
    },
    async nameSpeaker(meetingId, speakerId, name) {
      const meetingPath = `/v1/meetings/${encodeURIComponent(meetingId)}`;
      const meeting = await client.patch(`${meetingPath}/speakers/${encodeURIComponent(speakerId)}`, { name });
      return mapMeeting(
        meeting as Record<string, unknown>,
        (await transcript(client, meetingId)) as Record<string, unknown>[],
      );
    },
    async ask({ text }) {
      return mapAnswer(await client.post('/v1/query', { question: text, ui_locale: locale }));
    },
    async listChanges() {
      const response = await client.get<Page<unknown>>('/v1/graph/changes?limit=5');
      return response.items.map((item) => mapChange(item as Record<string, unknown>));
    },
    async getGraph(options) {
      const seed = options?.seedId ? `&seed_id=${encodeURIComponent(options.seedId)}` : '';
      return mapGraph(await client.get(`/v1/graph/subgraph?depth=1&limit=40&include_proposed=true${seed}`));
    },
  };
}
