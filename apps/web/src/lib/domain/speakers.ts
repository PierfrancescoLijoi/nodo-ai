import type { TFunction } from 'i18next';

import type { Speaker } from './types';

export type SpeakerNameError = 'required' | 'tooLong' | 'tooManyWords' | 'invalidCharacters';

// The same rules the API enforces (docs/API-V1.md), so the screen refuses before the server has to.
const ALLOWED_NAME = /^[\p{L}\p{N}\p{M} .'-]+$/u;
const MAX_LENGTH = 80;
const MAX_WORDS = 5;

export function validateSpeakerName(name: string): SpeakerNameError | null {
  if (!name.trim()) return 'required';
  if ([...name].length > MAX_LENGTH) return 'tooLong';
  if (!ALLOWED_NAME.test(name)) return 'invalidCharacters';
  if (name.trim().split(/ +/).length > MAX_WORDS) return 'tooManyWords';
  return null;
}

export const SPEAKER_ERROR_KEYS = {
  required: 'meeting.speakerNameRequired',
  tooLong: 'meeting.speakerNameTooLong',
  tooManyWords: 'meeting.speakerNameTooManyWords',
  invalidCharacters: 'meeting.speakerNameInvalidCharacters',
} as const satisfies Record<SpeakerNameError, string>;

export function speakerLabel(speakers: readonly Speaker[], id: string, t: TFunction): string {
  const name = speakers.find((speaker) => speaker.id === id)?.name;
  if (name) return name;
  return t('meeting.speakerFallback', { number: id.match(/\d+$/)?.[0] ?? id });
}
