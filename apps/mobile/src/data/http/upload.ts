import { ApiError } from './client';

type Fetch = typeof fetch;
type UploadMode = 'local' | 'azure';

interface UploadOptions {
  url: string;
  audio: Blob;
  mode: UploadMode;
  fetchImpl?: Fetch;
  onProgress?: (fraction: number) => void;
  singlePutLimit?: number;
  blockSize?: number;
}

const AZURE_SINGLE_PUT_LIMIT = 256 * 1024 * 1024;
const AZURE_BLOCK_SIZE = 4 * 1024 * 1024;

function blockId(index: number): string {
  const value = index.toString().padStart(8, '0');
  if (typeof btoa === 'function') return btoa(value);
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  let encoded = '';
  for (let cursor = 0; cursor < value.length; cursor += 3) {
    const first = value.charCodeAt(cursor);
    const second = value.charCodeAt(cursor + 1);
    const third = value.charCodeAt(cursor + 2);
    const bits = (first << 16) | ((Number.isNaN(second) ? 0 : second) << 8) | (Number.isNaN(third) ? 0 : third);
    encoded += alphabet[(bits >> 18) & 63];
    encoded += alphabet[(bits >> 12) & 63];
    encoded += Number.isNaN(second) ? '=' : alphabet[(bits >> 6) & 63];
    encoded += Number.isNaN(third) ? '=' : alphabet[bits & 63];
  }
  return encoded;
}

function query(url: string, suffix: string): string {
  return `${url}${url.includes('?') ? '&' : '?'}${suffix}`;
}

async function ensureUploadSucceeded(response: Response): Promise<void> {
  if (response.ok) return;
  throw new ApiError('Audio upload failed', response.status, 'upload_failed');
}

async function put(fetchImpl: Fetch, url: string, body: Blob, headers?: Record<string, string>): Promise<void> {
  const response = await fetchImpl(url, { method: 'PUT', body, ...(headers ? { headers } : {}) });
  await ensureUploadSucceeded(response);
}

export async function uploadAudioToUrl({
  url,
  audio,
  mode,
  fetchImpl = fetch,
  onProgress,
  singlePutLimit = AZURE_SINGLE_PUT_LIMIT,
  blockSize = AZURE_BLOCK_SIZE,
}: UploadOptions): Promise<void> {
  onProgress?.(0);
  if (mode === 'local') {
    await put(fetchImpl, url, audio);
    onProgress?.(1);
    return;
  }
  if (audio.size <= singlePutLimit) {
    await put(fetchImpl, url, audio, { 'x-ms-blob-type': 'BlockBlob' });
    onProgress?.(1);
    return;
  }

  const ids: string[] = [];
  for (let offset = 0, index = 0; offset < audio.size; offset += blockSize, index += 1) {
    const id = blockId(index);
    ids.push(id);
    await put(
      fetchImpl,
      query(url, `comp=block&blockid=${encodeURIComponent(id)}`),
      audio.slice(offset, offset + blockSize),
    );
    onProgress?.(Math.min(0.99, (offset + Math.min(blockSize, audio.size - offset)) / audio.size));
  }
  const xml = `<BlockList>${ids.map((id) => `<Latest>${id}</Latest>`).join('')}</BlockList>`;
  await put(fetchImpl, query(url, 'comp=blocklist'), new Blob([xml], { type: 'application/xml' }), {
    'Content-Type': 'application/xml',
  });
  onProgress?.(1);
}
