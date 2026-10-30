import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  addRecentClip,
  listRecentClips,
  MAX_RECENT_CLIPS,
  PROMPT_PREVIEW_CHARS,
  RECENT_CLIPS_KEY,
  type RecentClip,
  removeRecentClip,
} from '@/tools/video-generation/recentClips';

const HOUR_MS = 3_600_000;
const NOW = Date.parse('2026-10-30T12:00:00Z');
const TTL_HOURS = 24;

function clip(jobId: string, createdAt: number, prompt = `prompt ${jobId}`): RecentClip {
  return { job_id: jobId, created_at: new Date(createdAt).toISOString(), prompt };
}

function ids(clips: RecentClip[]): string[] {
  return clips.map((entry) => entry.job_id);
}

function storeClips(clips: RecentClip[]): void {
  localStorage.setItem(RECENT_CLIPS_KEY, JSON.stringify(clips));
}

function storedRaw(): unknown {
  return JSON.parse(localStorage.getItem(RECENT_CLIPS_KEY) ?? 'null');
}

const throwingStorage = {
  getItem: () => {
    throw new DOMException('denied', 'SecurityError');
  },
  setItem: () => {
    throw new DOMException('quota', 'QuotaExceededError');
  },
  removeItem: () => {
    throw new DOMException('denied', 'SecurityError');
  },
};

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('recent clips storage', () => {
  it('uses the video generation key and starts empty', () => {
    expect(RECENT_CLIPS_KEY).toBe('videokit.video_generation.recent');
    expect(listRecentClips(TTL_HOURS, NOW)).toEqual([]);
  });

  it('stores job_id, created_at and prompt as a JSON array', () => {
    addRecentClip(clip('job-a', NOW - HOUR_MS), TTL_HOURS, NOW);

    expect(storedRaw()).toEqual([
      {
        job_id: 'job-a',
        created_at: new Date(NOW - HOUR_MS).toISOString(),
        prompt: 'prompt job-a',
      },
    ]);
  });
});

describe('ordering', () => {
  it('lists the newest added clip first', () => {
    addRecentClip(clip('job-a', NOW), TTL_HOURS, NOW);
    addRecentClip(clip('job-b', NOW), TTL_HOURS, NOW);

    expect(ids(listRecentClips(TTL_HOURS, NOW))).toEqual(['job-b', 'job-a']);
  });

  it('moves an already listed job to the top instead of duplicating it', () => {
    addRecentClip(clip('job-a', NOW), TTL_HOURS, NOW);
    addRecentClip(clip('job-b', NOW), TTL_HOURS, NOW);
    addRecentClip(clip('job-a', NOW), TTL_HOURS, NOW);

    expect(ids(listRecentClips(TTL_HOURS, NOW))).toEqual(['job-a', 'job-b']);
  });
});

describe('cap', () => {
  it(`keeps at most ${MAX_RECENT_CLIPS} entries and drops the oldest`, () => {
    for (let index = 1; index <= MAX_RECENT_CLIPS + 1; index += 1) {
      addRecentClip(clip(`job-${index}`, NOW), TTL_HOURS, NOW);
    }

    const listed = ids(listRecentClips(TTL_HOURS, NOW));
    expect(listed).toHaveLength(MAX_RECENT_CLIPS);
    expect(listed[0]).toBe(`job-${MAX_RECENT_CLIPS + 1}`);
    expect(listed).not.toContain('job-1');
  });

  it('lists at most the cap even when storage holds more', () => {
    const oversized = Array.from({ length: MAX_RECENT_CLIPS + 5 }, (_, index) =>
      clip(`job-${index}`, NOW),
    );
    localStorage.setItem(RECENT_CLIPS_KEY, JSON.stringify(oversized));

    expect(listRecentClips(TTL_HOURS, NOW)).toHaveLength(MAX_RECENT_CLIPS);
  });
});

describe('prompt preview', () => {
  it(`keeps the first ${PROMPT_PREVIEW_CHARS} characters of the prompt`, () => {
    addRecentClip(clip('job-a', NOW, 'x'.repeat(200)), TTL_HOURS, NOW);

    expect(listRecentClips(TTL_HOURS, NOW)[0].prompt).toBe('x'.repeat(PROMPT_PREVIEW_CHARS));
  });

  it('never splits a surrogate pair when cutting the prompt', () => {
    const prompt = `${'a'.repeat(PROMPT_PREVIEW_CHARS - 1)}😀z`;
    addRecentClip(clip('job-a', NOW, prompt), TTL_HOURS, NOW);

    expect(listRecentClips(TTL_HOURS, NOW)[0].prompt).toBe(
      `${'a'.repeat(PROMPT_PREVIEW_CHARS - 1)}😀`,
    );
  });
});

describe('age pruning', () => {
  it('drops entries older than the job TTL using the given clock', () => {
    storeClips([clip('job-old', NOW - 25 * HOUR_MS), clip('job-fresh', NOW - HOUR_MS)]);

    expect(ids(listRecentClips(TTL_HOURS, NOW))).toEqual(['job-fresh']);
  });

  it('keeps an entry exactly one TTL old', () => {
    storeClips([clip('job-edge', NOW - TTL_HOURS * HOUR_MS)]);

    expect(ids(listRecentClips(TTL_HOURS, NOW))).toEqual(['job-edge']);
  });

  it('prunes against a later clock as time passes', () => {
    addRecentClip(clip('job-a', NOW), TTL_HOURS, NOW);

    expect(ids(listRecentClips(TTL_HOURS, NOW + 23 * HOUR_MS))).toEqual(['job-a']);
    expect(listRecentClips(TTL_HOURS, NOW + 25 * HOUR_MS)).toEqual([]);
  });

  it('drops expired entries when a new clip is added', () => {
    storeClips([clip('job-old', NOW - 48 * HOUR_MS)]);
    addRecentClip(clip('job-new', NOW), TTL_HOURS, NOW);

    expect(ids(listRecentClips(TTL_HOURS, NOW))).toEqual(['job-new']);
  });
});

describe('remove', () => {
  it('removes one job and keeps the rest in order', () => {
    addRecentClip(clip('job-a', NOW), TTL_HOURS, NOW);
    addRecentClip(clip('job-b', NOW), TTL_HOURS, NOW);
    addRecentClip(clip('job-c', NOW), TTL_HOURS, NOW);

    removeRecentClip('job-b');

    expect(ids(listRecentClips(TTL_HOURS, NOW))).toEqual(['job-c', 'job-a']);
  });
});

describe('malformed storage', () => {
  it('ignores malformed entries and keeps the valid ones', () => {
    const valid = clip('job-ok', NOW);
    localStorage.setItem(
      RECENT_CLIPS_KEY,
      JSON.stringify([
        valid,
        null,
        42,
        { job_id: 'no-date', prompt: 'p' },
        { job_id: 'bad-date', created_at: 'not a date', prompt: 'p' },
        { job_id: '', created_at: valid.created_at, prompt: 'p' },
        { job_id: 'no-prompt', created_at: valid.created_at },
      ]),
    );

    expect(listRecentClips(TTL_HOURS, NOW)).toEqual([valid]);
  });

  it('reads as empty when the stored text is not JSON or not an array', () => {
    localStorage.setItem(RECENT_CLIPS_KEY, '{not json');
    expect(listRecentClips(TTL_HOURS, NOW)).toEqual([]);

    localStorage.setItem(RECENT_CLIPS_KEY, JSON.stringify({ job_id: 'job-a' }));
    expect(listRecentClips(TTL_HOURS, NOW)).toEqual([]);
  });
});

describe('unavailable storage', () => {
  it('lists nothing and writes nothing without throwing when localStorage is missing', () => {
    vi.stubGlobal('localStorage', undefined);

    expect(listRecentClips(TTL_HOURS, NOW)).toEqual([]);
    expect(() => addRecentClip(clip('job-a', NOW), TTL_HOURS, NOW)).not.toThrow();
    expect(() => removeRecentClip('job-a')).not.toThrow();
  });

  it('lists nothing and never throws when every storage call throws', () => {
    vi.stubGlobal('localStorage', throwingStorage);

    expect(listRecentClips(TTL_HOURS, NOW)).toEqual([]);
    expect(() => addRecentClip(clip('job-a', NOW), TTL_HOURS, NOW)).not.toThrow();
    expect(() => removeRecentClip('job-a')).not.toThrow();
  });

  it('lists nothing and never throws when reading localStorage itself throws', () => {
    const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      get: () => {
        throw new DOMException('blocked', 'SecurityError');
      },
    });
    try {
      expect(listRecentClips(TTL_HOURS, NOW)).toEqual([]);
      expect(() => addRecentClip(clip('job-a', NOW), TTL_HOURS, NOW)).not.toThrow();
      expect(() => removeRecentClip('job-a')).not.toThrow();
    } finally {
      if (original) Object.defineProperty(globalThis, 'localStorage', original);
    }
  });
});
