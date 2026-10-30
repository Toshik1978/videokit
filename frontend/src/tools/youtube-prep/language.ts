/** The choice meaning "Same as the speech". */
export const AUTO_LANGUAGE = 'auto';

export const LANGUAGE_KEY = 'videokit.youtube_prep.language';

/** `localStorage`, or `undefined` when the browser refuses access (private mode, blocked data). */
function storage(): Storage | undefined {
  try {
    return globalThis.localStorage;
  } catch {
    return undefined;
  }
}

/** A language's name in itself (for example "Deutsch"); the code when the browser cannot name it. */
export function endonym(code: string): string {
  try {
    return new Intl.DisplayNames([code], { type: 'language' }).of(code) ?? code;
  } catch {
    return code;
  }
}

/** The remembered choice: a code in `supported`, else `"auto"`. Never throws. */
export function loadChoice(supported: readonly string[]): string {
  try {
    const stored = storage()?.getItem(LANGUAGE_KEY) ?? null;
    return stored !== null && supported.includes(stored) ? stored : AUTO_LANGUAGE;
  } catch {
    return AUTO_LANGUAGE;
  }
}

/** Remember the choice on this device. Never throws. */
export function saveChoice(value: string): void {
  try {
    storage()?.setItem(LANGUAGE_KEY, value);
  } catch {
    // Storage is a convenience; the choice just is not remembered.
  }
}

/** The codes ordered by their endonym; the input is left untouched. */
export function sortedLanguages(codes: readonly string[]): string[] {
  return [...codes].sort((a, b) => endonym(a).localeCompare(endonym(b)));
}

/** The browser's language as a lower-cased base subtag ("pt-BR" gives "pt"), or `undefined` when it reports none. */
export function browserLanguage(): string | undefined {
  const tag = globalThis.navigator?.language?.split(/[-_]/)[0]?.trim().toLowerCase();
  return tag ? tag : undefined;
}
