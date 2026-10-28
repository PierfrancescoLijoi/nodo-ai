// The routes that exist only on the local engine (/v1/local, ADR-014).
import type { z } from 'zod';

import type { ApiClient } from './client';
import { indexProgress, localStatus, syncProgress } from './schemas';
import { ApiError } from './client';

export interface LocalStatus {
  backend: string;
  missing: string[];
  pendingJobs: number;
  gpu: boolean;
}

export interface FailedMeeting {
  meetingId: string;
  reason: string;
}

export type RunState = 'idle' | 'running' | 'done' | 'failed';

export interface SyncProgress {
  state: RunState;
  total: number;
  done: number;
  copied: number;
  unchanged: number;
  deleted: number;
  failed: FailedMeeting[];
  message: string;
}

export type IndexMode = 'verify' | 'repair';

export const INDEX_KINDS = [
  'missing_from_index',
  'partial_index',
  'stale_index',
  'leftover_index',
  'leftover_graph',
  'dangling_evidence',
  'entity_vectors_missing',
] as const;
export type IndexFindingKind = (typeof INDEX_KINDS)[number] | 'other';

export interface IndexProgress {
  state: RunState;
  mode: IndexMode | '';
  checked: number;
  skipped: number;
  repaired: number;
  findings: { kind: IndexFindingKind; meetingId: string }[];
  failed: FailedMeeting[];
  message: string;
}

export interface LocalEngineApi {
  getStatus(): Promise<LocalStatus>;
  startSync(input: { cloudUrl: string; accessToken: string; force: boolean }): Promise<SyncProgress>;
  getSync(): Promise<SyncProgress>;
  startIndex(mode: IndexMode): Promise<IndexProgress>;
  getIndex(): Promise<IndexProgress>;
}

function parse<S extends z.ZodType>(schema: S, value: unknown): z.infer<S> {
  const result = schema.safeParse(value);
  if (!result.success) throw new ApiError('Unexpected API response', 502, 'invalid_response');
  return result.data;
}

const failures = (items: { meeting_id: string; reason: string }[]): FailedMeeting[] =>
  items.map((item) => ({ meetingId: item.meeting_id, reason: item.reason }));

export function mapSync(value: unknown): SyncProgress {
  const item = parse(syncProgress, value);
  return {
    state: item.state,
    total: item.total,
    done: item.done,
    copied: item.pushed,
    unchanged: item.unchanged,
    deleted: item.deleted,
    failed: failures(item.failed),
    message: item.message,
  };
}

const isKind = (value: string): value is (typeof INDEX_KINDS)[number] =>
  (INDEX_KINDS as readonly string[]).includes(value);

export function mapIndex(value: unknown): IndexProgress {
  const item = parse(indexProgress, value);
  return {
    state: item.state,
    mode: item.mode === 'verify' || item.mode === 'repair' ? item.mode : '',
    checked: item.checked,
    skipped: item.skipped,
    repaired: item.repaired,
    findings: item.findings.map((finding) => ({
      kind: isKind(finding.kind) ? finding.kind : 'other',
      meetingId: finding.meeting_id,
    })),
    failed: failures(item.failed),
    message: item.message,
  };
}

export function createLocalEngineApi(client: ApiClient): LocalEngineApi {
  return {
    async getStatus() {
      const item = parse(localStatus, await client.get('/v1/local/status'));
      return { backend: item.backend, missing: item.missing, pendingJobs: item.pending_jobs, gpu: item.gpu };
    },
    async startSync({ cloudUrl, accessToken, force }) {
      return mapSync(
        await client.post('/v1/local/sync', { cloud_url: cloudUrl, access_token: accessToken, force }),
      );
    },
    async getSync() {
      return mapSync(await client.get('/v1/local/sync'));
    },
    async startIndex(mode) {
      return mapIndex(await client.post('/v1/local/index', { mode }));
    },
    async getIndex() {
      return mapIndex(await client.get('/v1/local/index'));
    },
  };
}
