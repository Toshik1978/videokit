import { describe, expect, it } from 'vitest';

import en from '@/i18n/en.json';

const FAILURE_CODES = [
  'provider_out_of_credit',
  'content_blocked',
  'provider_timeout',
  'invalid_picture',
  'provider_unavailable',
  'resume_impossible',
  'video_failed',
] as const;

const ERROR_CODES = ['too_many_pictures', 'picture_too_large', 'invalid_picture'] as const;

describe('Video Generation catalog', () => {
  it('describes the tool on the home page', () => {
    expect(en.tools.video_generation.name).toBe('Video Generation');
    expect(en.tools.video_generation.description).toBeTruthy();
  });

  it.each(FAILURE_CODES)('has a message for failure code %s', (code) => {
    expect((en.job.failures as Record<string, string>)[code], code).toBeTruthy();
  });

  it.each(ERROR_CODES)('has a message for API error code %s', (code) => {
    expect((en.errors.codes as Record<string, string>)[code], code).toBeTruthy();
  });

  it('tells a user whose provider ran out of credit to top up the account', () => {
    expect(en.job.failures.provider_out_of_credit).toMatch(/top up/i);
  });

  it('keeps the shared generating label unchanged for YouTube Prep', () => {
    expect(en.job.state.generating).toBe('Generating metadata');
  });

  it('has its own generating stage label that does not name metadata', () => {
    expect(en.videoGeneration.stage.generating).toBeTruthy();
    expect(en.videoGeneration.stage.generating).not.toMatch(/metadata/i);
  });

  it('warns that cancelling a generating job may still be charged', () => {
    expect(en.videoGeneration.cancelWarning).toBe(
      'The provider may still charge for this attempt.',
    );
  });

  it('shows the retention note and the estimate with their values', () => {
    expect(en.videoGeneration.retention).toContain('{hours}');
    expect(en.videoGeneration.estimate).toContain('{cost}');
    expect(en.videoGeneration.estimateUnknown).toBeTruthy();
  });

  it('has no empty strings', () => {
    const empty: string[] = [];
    const walk = (node: unknown, path: string): void => {
      if (typeof node === 'string') {
        if (node.trim() === '') empty.push(path);
      } else if (node && typeof node === 'object') {
        for (const [key, value] of Object.entries(node)) walk(value, `${path}.${key}`);
      }
    };
    walk(en.videoGeneration, 'videoGeneration');
    expect(empty).toEqual([]);
  });
});
