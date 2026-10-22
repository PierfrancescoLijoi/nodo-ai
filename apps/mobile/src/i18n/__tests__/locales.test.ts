import da from '../locales/da';
import en from '../locales/en';
import itLocale from '../locales/it';
import pl from '../locales/pl';

type Tree = { [key: string]: string | Tree };

const PLURAL_SUFFIX = /_(zero|one|two|few|many|other)$/;
const REQUIRED_FORMS: Record<string, string[]> = {
  en: ['one', 'other'],
  it: ['one', 'other'],
  da: ['one', 'other'],
  pl: ['one', 'few', 'many', 'other'],
};
const locales: Record<string, Tree> = { en, it: itLocale, da, pl };

function flatten(tree: Tree, prefix = ''): Record<string, string> {
  return Object.entries(tree).reduce<Record<string, string>>((acc, [key, value]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    return typeof value === 'string' ? { ...acc, [path]: value } : { ...acc, ...flatten(value, path) };
  }, {});
}

const baseKey = (key: string) => key.replace(PLURAL_SUFFIX, '');
const placeholders = (text: string) => [...text.matchAll(/{{\s*(\w+)\s*}}/g)].map((m) => m[1]).sort();

const reference = flatten(en);
const referenceKeys = [...new Set(Object.keys(reference).map(baseKey))].sort();

describe.each(['pl', 'it', 'da'])('locale %s', (code) => {
  const flat = flatten(locales[code] as Tree);

  it('has exactly the keys of English (plural forms aside)', () => {
    const keys = [...new Set(Object.keys(flat).map(baseKey))].sort();
    expect(keys).toEqual(referenceKeys);
  });

  it('has every plural form its language needs', () => {
    const pluralBases = [...new Set(Object.keys(reference).filter((k) => PLURAL_SUFFIX.test(k)).map(baseKey))];
    for (const base of pluralBases) {
      for (const form of REQUIRED_FORMS[code] as string[]) {
        expect(flat[`${base}_${form}`]).toBeTruthy();
      }
    }
  });

  it('keeps the same interpolation placeholders', () => {
    for (const [key, text] of Object.entries(flat)) {
      const counterpart = reference[key] ?? reference[`${baseKey(key)}_other`];
      expect(placeholders(text)).toEqual(placeholders(counterpart as string));
    }
  });
});

describe('all locales', () => {
  it('contain no empty strings and no em or en dashes', () => {
    for (const [code, tree] of Object.entries(locales)) {
      for (const [key, text] of Object.entries(flatten(tree))) {
        expect({ code, key, empty: text.trim() === '' }).toEqual({ code, key, empty: false });
        expect({ code, key, dash: /[–—]/.test(text) }).toEqual({ code, key, dash: false });
      }
    }
  });
});
