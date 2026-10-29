import type { TFunction } from 'i18next';

import type { Speaker } from './types';

export type SpeakerNameValidationError = 'required' | 'tooLong' | 'tooManyWords' | 'invalidCharacters';

const ALLOWED_NAME = /^[\p{L}\p{N}\p{M} .'-]+$/u;

export function validateSpeakerName(name: string | null): SpeakerNameValidationError | null {
  if (name === null) return null;
  if (!name.trim()) return 'required';
  if ([...name].length > 80) return 'tooLong';
  if (!ALLOWED_NAME.test(name)) return 'invalidCharacters';
  if (name.trim().split(/ +/).length > 5) return 'tooManyWords';
  return null;
}

export function speakerLabel(speakers: Speaker[], id: string, t: TFunction): string {
  const name = speakers.find((speaker) => speaker.id === id)?.name;
  if (name) return name;
  return t('meeting.speakerFallback', { number: id.match(/\d+$/)?.[0] ?? id });
}
