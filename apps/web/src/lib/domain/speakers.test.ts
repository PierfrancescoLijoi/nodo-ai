import { describe, expect, it } from 'vitest';

import { createI18n } from '../i18n';
import { speakerLabel, validateSpeakerName } from './speakers';

describe('validateSpeakerName', () => {
  it.each([
    ['Marco Rossi', null],
    ["Jean-Luc O'Neil Jr.", null],
    ['Łukasz Żółć', null],
    ['', 'required'],
    ['   ', 'required'],
    ['x'.repeat(81), 'tooLong'],
    ['a b c d e f', 'tooManyWords'],
    ['bad <name>', 'invalidCharacters'],
    ['line\nbreak', 'invalidCharacters'],
    ['semi;colon', 'invalidCharacters'],
  ])('%j -> %s', (name, expected) => expect(validateSpeakerName(name)).toBe(expected));

  it('counts characters, not UTF-16 units', () => {
    expect(validateSpeakerName('𝒜'.repeat(80))).toBeNull();
  });
});

describe('speakerLabel', () => {
  const t = createI18n('en').t;
  const speakers = [
    { id: 'S1', name: 'Marco', segments: 1, talkMs: 1 },
    { id: 'S2', name: null, segments: 1, talkMs: 1 },
  ];

  it('prefers the name the owner gave', () => expect(speakerLabel(speakers, 'S1', t)).toBe('Marco'));
  it('falls back to a numbered speaker', () => expect(speakerLabel(speakers, 'S2', t)).toBe('Speaker 2'));
  it('copes with an id nobody knows', () => expect(speakerLabel(speakers, 'S7', t)).toBe('Speaker 7'));
});
