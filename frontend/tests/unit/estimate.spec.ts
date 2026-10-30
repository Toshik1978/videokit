import { describe, expect, it } from 'vitest';

import { estimateCost, formatCost } from '@/tools/video-generation/estimate';
import type { VideoOptions } from '@/tools/video-generation/types';

function options(overrides: Partial<VideoOptions> = {}): VideoOptions {
  return {
    durations_s: [5, 6, 7, 8, 9, 10],
    default_duration_s: 8,
    aspect_ratios: ['9:16', '16:9'],
    resolutions: ['1080p', '720p'],
    max_pictures: 7,
    max_picture_bytes: 10_000_000,
    picture_types: ['image/jpeg', 'image/png'],
    max_prompt_chars: 2000,
    price_per_second_usd: { '720p': 0.1, '1080p': 0.15 },
    price_per_picture_usd: 0.002,
    ...overrides,
  };
}

describe('estimateCost', () => {
  it('is duration times the per-second price of the resolution plus the pictures', () => {
    expect(estimateCost(options(), 8, '1080p', 3)).toBeCloseTo(8 * 0.15 + 3 * 0.002, 10);
    expect(estimateCost(options(), 5, '720p', 0)).toBeCloseTo(0.5, 10);
  });

  it('is unknown when the per-second prices are unknown', () => {
    expect(estimateCost(options({ price_per_second_usd: null }), 8, '1080p', 0)).toBeNull();
  });

  it('is unknown when the chosen resolution has no price', () => {
    const partial = options({ price_per_second_usd: { '720p': 0.1 } });
    expect(estimateCost(partial, 8, '1080p', 0)).toBeNull();
  });

  it('is unknown when pictures are added and the picture price is unknown', () => {
    const unknownPicture = options({ price_per_picture_usd: null });
    expect(estimateCost(unknownPicture, 8, '720p', 2)).toBeNull();
  });

  it('ignores an unknown picture price while there are no pictures', () => {
    const unknownPicture = options({ price_per_picture_usd: null });
    expect(estimateCost(unknownPicture, 8, '720p', 0)).toBeCloseTo(0.8, 10);
  });
});

describe('formatCost', () => {
  it('shows dollars with at least two and at most four decimals', () => {
    expect(formatCost(1.2)).toBe('$1.20');
    expect(formatCost(0.0016)).toBe('$0.0016');
  });
});
