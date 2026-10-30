/** One device-local Recent clips entry (data-model.md "Recent clip entry"). */
export interface RecentClip {
  job_id: string;
  /** ISO-8601 time the job was started. */
  created_at: string;
  /** The prompt's first characters. */
  prompt: string;
}

export const RECENT_CLIPS_KEY = 'videokit.video_generation.recent';
export const MAX_RECENT_CLIPS = 20;
export const PROMPT_PREVIEW_CHARS = 80;

const HOUR_MS = 3_600_000;

/** `localStorage`, or `undefined` when the browser refuses access (private mode, blocked data). */
function storage(): Storage | undefined {
  try {
    return globalThis.localStorage;
  } catch {
    return undefined;
  }
}

function isRecentClip(value: unknown): value is RecentClip {
  if (typeof value !== 'object' || value === null) return false;
  const { job_id, created_at, prompt } = value as Record<string, unknown>;
  return (
    typeof job_id === 'string' &&
    job_id !== '' &&
    typeof created_at === 'string' &&
    !Number.isNaN(Date.parse(created_at)) &&
    typeof prompt === 'string'
  );
}

/** Stored entries, newest first. Unreadable storage and malformed entries yield none. */
function readStored(): RecentClip[] {
  try {
    const raw = storage()?.getItem(RECENT_CLIPS_KEY) ?? null;
    if (raw === null) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter(isRecentClip) : [];
  } catch {
    return [];
  }
}

function writeStored(clips: RecentClip[]): void {
  try {
    storage()?.setItem(RECENT_CLIPS_KEY, JSON.stringify(clips));
  } catch {
    // Quota or blocked storage: the clip still shows on its page, just not in the list.
  }
}

/** Stored entries that are not older than the job TTL, capped at MAX_RECENT_CLIPS. */
function freshClips(jobTtlHours: number, now: number): RecentClip[] {
  const cutoff = now - jobTtlHours * HOUR_MS;
  return readStored()
    .filter((clip) => Date.parse(clip.created_at) >= cutoff)
    .slice(0, MAX_RECENT_CLIPS);
}

/** The device's recent clips, newest first, without entries older than `jobTtlHours`. */
export function listRecentClips(jobTtlHours: number, now: number = Date.now()): RecentClip[] {
  return freshClips(jobTtlHours, now);
}

/**
 * Puts a clip at the top of the list. A job already listed moves up instead of repeating, and the
 * oldest entries drop off past the cap.
 */
export function addRecentClip(
  clip: RecentClip,
  jobTtlHours: number,
  now: number = Date.now(),
): void {
  const entry: RecentClip = {
    job_id: clip.job_id,
    created_at: clip.created_at,
    prompt: Array.from(clip.prompt).slice(0, PROMPT_PREVIEW_CHARS).join(''),
  };
  const others = freshClips(jobTtlHours, now).filter((listed) => listed.job_id !== entry.job_id);
  writeStored([entry, ...others].slice(0, MAX_RECENT_CLIPS));
}

export function removeRecentClip(jobId: string): void {
  writeStored(readStored().filter((clip) => clip.job_id !== jobId));
}
