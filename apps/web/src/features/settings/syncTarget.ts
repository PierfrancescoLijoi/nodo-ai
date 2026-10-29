import { isAllowedUrl } from '@/lib/runtime/config';
import { safeGet, safeSet, type StorageLike } from '@/lib/runtime/storage';
import type { AzureProfile } from '@/lib/runtime/connection';

export const SYNC_TARGET_KEY = 'nodo.azureTarget';
export const SYNC_CHANNEL = 'nodo-sync-auth';

export const EMPTY_TARGET: AzureProfile = { apiBaseUrl: '', issuer: '', clientId: '', scope: '' };

/** A complete, safe target (https, or http on this machine), or undefined. */
export function normalizeTarget(value: Partial<AzureProfile>): AzureProfile | undefined {
  const apiBaseUrl = (value.apiBaseUrl ?? '').trim().replace(/\/+$/, '');
  const issuer = (value.issuer ?? '').trim().replace(/\/+$/, '');
  const clientId = (value.clientId ?? '').trim();
  const scope = (value.scope ?? '').trim();
  if (!isAllowedUrl(apiBaseUrl) || !isAllowedUrl(issuer) || !clientId || !scope) return undefined;
  return { apiBaseUrl, issuer, clientId, scope };
}

export function readTarget(storage: StorageLike | undefined): AzureProfile {
  try {
    const raw = safeGet(storage, SYNC_TARGET_KEY);
    return raw ? { ...EMPTY_TARGET, ...(JSON.parse(raw) as Partial<AzureProfile>) } : EMPTY_TARGET;
  } catch {
    return EMPTY_TARGET;
  }
}

export function saveTarget(storage: StorageLike | undefined, target: AzureProfile): void {
  safeSet(storage, SYNC_TARGET_KEY, JSON.stringify(target));
}

export interface SyncTokenMessage {
  type: 'nodo:sync-token';
  token: string;
}

export const isSyncTokenMessage = (value: unknown): value is SyncTokenMessage =>
  typeof value === 'object' &&
  value !== null &&
  (value as SyncTokenMessage).type === 'nodo:sync-token' &&
  typeof (value as SyncTokenMessage).token === 'string';
