import i18n from '@/i18n';

import { speakerLabel, validateSpeakerName } from '../speakerName';

describe('validateSpeakerName', () => {
  it.each(['Ada', 'Ada Lovelace', "Renée O'Connor", 'João-Paulo', '张 伟', 'Mårten 2.'])('accepts %s', (name) => {
    expect(validateSpeakerName(name)).toBeNull();
  });

  it('accepts null for clearing a name', () => {
    expect(validateSpeakerName(null)).toBeNull();
  });

  it.each([
    ['', 'required'],
    ['   ', 'required'],
    ['a'.repeat(81), 'tooLong'],
    ['one two three four five six', 'tooManyWords'],
    ['Ada_Lovelace', 'invalidCharacters'],
    ['Ada\nLovelace', 'invalidCharacters'],
  ] as const)('rejects %j as %s', (name, error) => {
    expect(validateSpeakerName(name)).toBe(error);
  });
});

describe('speakerLabel', () => {
  it('uses the saved name when one exists', () => {
    expect(speakerLabel([{ id: 'S2', name: 'Ada', segments: 2, talkMs: 500 }], 'S2', i18n.t)).toBe('Ada');
  });

  it('builds a localized fallback from the speaker id number', () => {
    expect(speakerLabel([], 'S12', i18n.t)).toBe('Speaker 12');
  });
});
