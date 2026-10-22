import type { Locale } from '@/i18n';

import type { MeetingsRepository } from '../types';
import { ApiError, type ApiClient } from './client';
import { mapAnswer, mapChange, mapGraph, mapMeeting, mapMeetingSummary } from './mappers';

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

export function createHttpRepository(client: ApiClient, locale: Locale): MeetingsRepository {
  return {
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
