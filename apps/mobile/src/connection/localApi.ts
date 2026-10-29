import { createApiClient, type ApiClient } from '@/data/http/client';

export interface LocalStatus {
  backend: string;
  missing: string[];
  pendingJobs: number;
  gpu: boolean;
}

export interface SyncFailure {
  meetingId: string;
  reason: string;
}

export interface SyncProgress {
  state: 'idle' | 'running' | 'done' | 'failed';
  total: number;
  done: number;
  copied: number;
  unchanged: number;
  deleted: number;
  failed: SyncFailure[];
  message: string;
}

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
const number = (value: unknown) => (typeof value === 'number' ? value : 0);

export function mapSyncProgress(value: unknown): SyncProgress {
  const item = record(value);
  const state = ['idle', 'running', 'done', 'failed'].includes(String(item.state))
    ? (item.state as SyncProgress['state'])
    : 'failed';
  const failures = Array.isArray(item.failed) ? item.failed : [];
  return {
    state,
    total: number(item.total),
    done: number(item.done),
    copied: number(item.pushed),
    unchanged: number(item.unchanged),
    deleted: number(item.deleted),
    failed: failures.map(record).map((failure) => ({
      meetingId: String(failure.meeting_id ?? ''),
      reason: String(failure.reason ?? ''),
    })),
    message: String(item.message ?? ''),
  };
}

function mapStatus(value: unknown): LocalStatus {
  const item = record(value);
  return {
    backend: String(item.backend ?? 'local'),
    missing: Array.isArray(item.missing) ? item.missing.map(String) : [],
    pendingJobs: number(item.pending_jobs),
    gpu: item.gpu === true,
  };
}

export type IndexMode = 'verify' | 'repair';

export type IndexFindingKind =
  | 'missing_from_index'
  | 'partial_index'
  | 'stale_index'
  | 'leftover_index'
  | 'leftover_graph'
  | 'dangling_evidence'
  | 'entity_vectors_missing';

export interface IndexProgress {
  state: 'idle' | 'running' | 'done' | 'failed';
  mode: IndexMode | '';
  checked: number;
  skipped: number;
  repaired: number;
  findings: { kind: IndexFindingKind | 'other'; meetingId: string }[];
  failed: SyncFailure[];
  message: string;
}

const INDEX_KINDS: IndexFindingKind[] = [
  'missing_from_index',
  'partial_index',
  'stale_index',
  'leftover_index',
  'leftover_graph',
  'dangling_evidence',
  'entity_vectors_missing',
];

export function mapIndexProgress(value: unknown): IndexProgress {
  const item = record(value);
  const state = ['idle', 'running', 'done', 'failed'].includes(String(item.state))
    ? (item.state as IndexProgress['state'])
    : 'failed';
  return {
    state,
    mode: item.mode === 'verify' || item.mode === 'repair' ? item.mode : '',
    checked: number(item.checked),
    skipped: number(item.skipped),
    repaired: number(item.repaired),
    findings: (Array.isArray(item.findings) ? item.findings : []).map(record).map((finding) => ({
      kind: INDEX_KINDS.includes(finding.kind as IndexFindingKind) ? (finding.kind as IndexFindingKind) : 'other',
      meetingId: String(finding.meeting_id ?? ''),
    })),
    failed: (Array.isArray(item.failed) ? item.failed : []).map(record).map((failure) => ({
      meetingId: String(failure.meeting_id ?? ''),
      reason: String(failure.reason ?? ''),
    })),
    message: String(item.message ?? ''),
  };
}

export interface LocalEngineApi {
  getStatus(): Promise<LocalStatus>;
  startSync(input: { cloudUrl: string; accessToken: string; force: boolean }): Promise<SyncProgress>;
  getSync(): Promise<SyncProgress>;
  startIndex(mode: IndexMode): Promise<IndexProgress>;
  getIndex(): Promise<IndexProgress>;
}

export function createLocalEngineApi(client: ApiClient): LocalEngineApi {
  return {
    async getStatus() {
      return mapStatus(await client.get('/v1/local/status'));
    },
    async startSync({ cloudUrl, accessToken, force }) {
      return mapSyncProgress(
        await client.post('/v1/local/sync', { cloud_url: cloudUrl, access_token: accessToken, force }),
      );
    },
    async getSync() {
      return mapSyncProgress(await client.get('/v1/local/sync'));
    },
    async startIndex(mode) {
      return mapIndexProgress(await client.post('/v1/local/index', { mode }));
    },
    async getIndex() {
      return mapIndexProgress(await client.get('/v1/local/index'));
    },
  };
}

export function localEngineApi(baseUrl: string, token: string): LocalEngineApi {
  return createLocalEngineApi(createApiClient({ baseUrl, getAccessToken: async () => token }));
}
