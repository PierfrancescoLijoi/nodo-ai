import type { z } from 'zod';

import type {
  Answer,
  Change,
  ExportJob,
  Graph,
  Meeting,
  MeetingState,
  MeetingSummary,
  NewMeeting,
  ReviewDecision,
  ReviewItem,
} from '../domain/types';
import type { Locale } from '../i18n';
import { ApiError, type ApiClient } from './client';
import { mapAnswer, mapChange, mapGraph, mapMeeting, mapMeetingSummary, mapReviews } from './mappers';
import * as wire from './schemas';
import { uploadAudioToUrl, type UploadMode } from './upload';

export interface Repository {
  createMeeting(input: NewMeeting): Promise<{ id: string }>;
  uploadAudio(meetingId: string, audio: Blob, onProgress?: (fraction: number) => void): Promise<void>;
  getMeetingState(id: string): Promise<MeetingState>;
  deleteMeeting(id: string): Promise<void>;
  listMeetings(): Promise<MeetingSummary[]>;
  /** `null` when the meeting does not exist (or is not the caller's). */
  getMeeting(id: string): Promise<Meeting | null>;
  nameSpeaker(meetingId: string, speakerId: string, name: string | null): Promise<Meeting>;
  listChanges(): Promise<Change[]>;
  ask(question: string, locale: Locale): Promise<Answer>;
  getGraph(options?: { seedId?: string }): Promise<Graph>;
  listReviews(): Promise<ReviewItem[]>;
  reviewRelation(id: string, decision: ReviewDecision): Promise<void>;
  startExport(): Promise<ExportJob>;
  getExport(id: string): Promise<ExportJob>;
  downloadExport(id: string): Promise<Blob>;
}

/** The notice the recording declaration refers to (settings.known_notice_versions in the API). */
export const NOTICE_VERSION = '2026-10-v1';
/** A question may wait for a cold language model on the local engine (about 20 s). */
const QUERY_TIMEOUT_MS = 90_000;
const PAGE_SIZE = 50;
const MAX_PAGES = 4;
const SEGMENT_PAGE = 200;

/** A response that does not match the contract is reported as an API error, never passed on half-formed. */
function parse<S extends z.ZodType>(schema: S, value: unknown): z.infer<S> {
  const result = schema.safeParse(value);
  if (!result.success) throw new ApiError('Unexpected API response', 502, 'invalid_response');
  return result.data;
}

interface Options {
  mode: UploadMode;
  fetchImpl?: typeof fetch;
  createIdempotencyKey?: () => string;
}

export function createRepository(
  client: ApiClient,
  { mode, fetchImpl, createIdempotencyKey }: Options,
): Repository {
  const newKey = createIdempotencyKey ?? (() => crypto.randomUUID());

  async function transcript(id: string) {
    const items: z.infer<typeof wire.segmentPage>['items'] = [];
    for (let offset = 0; ; offset += SEGMENT_PAGE) {
      const page = parse(
        wire.segmentPage,
        await client.get(
          `/v1/meetings/${encodeURIComponent(id)}/transcript?limit=${SEGMENT_PAGE}&offset=${offset}`,
        ),
      );
      items.push(...page.items);
      if (page.items.length < SEGMENT_PAGE) return items;
    }
  }

  async function exportJob(raw: unknown): Promise<ExportJob> {
    const job = parse(wire.exportJob, raw);
    return { id: job.job_id, status: job.status };
  }

  return {
    async createMeeting({ title, locale, retentionDays }) {
      const created = parse(
        wire.createdMeeting,
        await client.post(
          '/v1/meetings',
          {
            recording_attestation: {
              notice_version: NOTICE_VERSION,
              locale,
              retention_policy_id: `retention-${retentionDays}d`,
              acknowledged: true,
            },
            ...(title ? { title } : {}),
          },
          { headers: { 'Idempotency-Key': newKey() } },
        ),
      );
      return { id: created.id };
    },

    async uploadAudio(meetingId, audio, onProgress) {
      const encoded = encodeURIComponent(meetingId);
      const session = parse(
        wire.uploadSession,
        await client.post(`/v1/meetings/${encoded}/upload-sessions`, {}),
      );
      await uploadAudioToUrl({ url: session.upload_url, audio, mode, fetchImpl, onProgress });
      await client.post(`/v1/meetings/${encoded}/complete`, {});
    },

    async getMeetingState(id) {
      return parse(wire.meetingStateOnly, await client.get(`/v1/meetings/${encodeURIComponent(id)}`)).state;
    },

    async deleteMeeting(id) {
      await client.delete(`/v1/meetings/${encodeURIComponent(id)}`);
    },

    async listMeetings() {
      const items: MeetingSummary[] = [];
      let cursor: string | undefined;
      for (let page = 0; page < MAX_PAGES; page += 1) {
        const suffix = cursor ? `&cursor=${encodeURIComponent(cursor)}` : '';
        const result = parse(wire.meetingPage, await client.get(`/v1/meetings?limit=${PAGE_SIZE}${suffix}`));
        items.push(...result.items.map(mapMeetingSummary));
        if (!result.next_cursor) break;
        cursor = result.next_cursor;
      }
      return items;
    },

    async getMeeting(id) {
      const encoded = encodeURIComponent(id);
      try {
        const [meeting, segments] = await Promise.all([
          client.get(`/v1/meetings/${encoded}`),
          transcript(id),
        ]);
        return mapMeeting(parse(wire.meetingDetail, meeting), segments);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },

    async nameSpeaker(meetingId, speakerId, name) {
      const base = `/v1/meetings/${encodeURIComponent(meetingId)}`;
      const meeting = await client.patch(`${base}/speakers/${encodeURIComponent(speakerId)}`, { name });
      return mapMeeting(parse(wire.meetingDetail, meeting), await transcript(meetingId));
    },

    async ask(question, locale) {
      const raw = await client.post(
        '/v1/query',
        { question, ui_locale: locale },
        { timeoutMs: QUERY_TIMEOUT_MS },
      );
      return mapAnswer(parse(wire.answer, raw));
    },

    async listChanges() {
      return parse(wire.changes, await client.get('/v1/graph/changes?limit=5')).items.map(mapChange);
    },

    async listReviews() {
      return mapReviews(parse(wire.subgraph, await client.get('/v1/graph/reviews?limit=100')));
    },

    async reviewRelation(id, decision) {
      await client.patch(`/v1/graph/relations/${encodeURIComponent(id)}`, { status: decision });
    },

    async getGraph(options) {
      const seed = options?.seedId ? `&seed_id=${encodeURIComponent(options.seedId)}` : '';
      return mapGraph(
        parse(
          wire.subgraph,
          await client.get(`/v1/graph/subgraph?depth=1&limit=40&include_proposed=true${seed}`),
        ),
      );
    },

    async startExport() {
      return exportJob(await client.post('/v1/export', {}));
    },

    async getExport(id) {
      return exportJob(await client.get(`/v1/export/${encodeURIComponent(id)}`));
    },

    downloadExport(id) {
      return client.blob(`/v1/export/${encodeURIComponent(id)}/download`);
    },
  };
}
