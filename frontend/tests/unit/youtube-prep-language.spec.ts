import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  AUTO_LANGUAGE,
  browserLanguage,
  endonym,
  LANGUAGE_KEY,
  loadChoice,
  saveChoice,
  sortedLanguages,
} from '@/tools/youtube-prep/language';

const SUPPORTED = ['de', 'fr', 'ja'];

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('endonym', () => {
  it('names a language in itself', () => {
    expect(endonym('de')).toBe('Deutsch');
    expect(endonym('fr')).toBe('français');
  });

  it('falls back to the code when DisplayNames throws', () => {
    vi.stubGlobal(
      'Intl',
      Object.assign(Object.create(Intl), {
        DisplayNames: class {
          constructor() {
            throw new RangeError('bad');
          }
        },
      }),
    );
    expect(endonym('de')).toBe('de');
  });

  it('falls back to the code when DisplayNames is unavailable', () => {
    vi.stubGlobal('Intl', Object.assign(Object.create(Intl), { DisplayNames: undefined }));
    expect(endonym('de')).toBe('de');
  });

  it('falls back to the code when the name is missing', () => {
    vi.stubGlobal(
      'Intl',
      Object.assign(Object.create(Intl), {
        DisplayNames: class {
          of(): undefined {
            return undefined;
          }
        },
      }),
    );
    expect(endonym('de')).toBe('de');
  });
});

describe('choice storage', () => {
  it('uses the documented key and defaults to auto', () => {
    expect(LANGUAGE_KEY).toBe('videokit.youtube_prep.language');
    expect(AUTO_LANGUAGE).toBe('auto');
    expect(loadChoice(SUPPORTED)).toBe('auto');
  });

  it('round-trips a supported code and auto', () => {
    saveChoice('ja');
    expect(localStorage.getItem(LANGUAGE_KEY)).toBe('ja');
    expect(loadChoice(SUPPORTED)).toBe('ja');
    saveChoice('auto');
    expect(loadChoice(SUPPORTED)).toBe('auto');
  });

  it('gives auto for an unsupported stored value', () => {
    localStorage.setItem(LANGUAGE_KEY, 'xx');
    expect(loadChoice(SUPPORTED)).toBe('auto');
  });

  it('gives auto when storage throws on read', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(loadChoice(SUPPORTED)).toBe('auto');
  });

  it('does not throw when storage throws on write', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('full');
    });
    expect(() => saveChoice('de')).not.toThrow();
  });

  it('survives an inaccessible localStorage', () => {
    vi.spyOn(globalThis, 'localStorage', 'get').mockImplementation(() => {
      throw new Error('denied');
    });
    expect(loadChoice(SUPPORTED)).toBe('auto');
    expect(() => saveChoice('de')).not.toThrow();
  });
});

describe('sortedLanguages', () => {
  it('sorts codes by endonym without mutating the input', () => {
    const codes = ['ja', 'fr', 'de'];
    const names = codes.map(endonym).sort((a, b) => a.localeCompare(b));
    expect(sortedLanguages(codes).map(endonym)).toEqual(names);
    expect(codes).toEqual(['ja', 'fr', 'de']);
  });
});

describe('browserLanguage', () => {
  it('returns the base subtag of the browser language, lower-cased', () => {
    vi.stubGlobal('navigator', { language: 'pt-BR' });
    expect(browserLanguage()).toBe('pt');
    vi.stubGlobal('navigator', { language: 'DE' });
    expect(browserLanguage()).toBe('de');
  });

  it('is undefined when the browser reports none', () => {
    vi.stubGlobal('navigator', { language: '' });
    expect(browserLanguage()).toBeUndefined();
    vi.stubGlobal('navigator', {});
    expect(browserLanguage()).toBeUndefined();
  });
});
