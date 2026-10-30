import type { VideoOptions } from './types';

const money = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});

/**
 * Estimated cost in USD: `duration x price per second of the resolution + pictures x price per
 * picture`. `null` when a price the choice needs is unknown; the picture price only counts once
 * there is at least one picture.
 */
export function estimateCost(
  options: VideoOptions,
  durationS: number,
  resolution: string,
  pictureCount: number,
): number | null {
  const perSecond = options.price_per_second_usd?.[resolution];
  if (perSecond === undefined) return null;
  if (pictureCount === 0) return durationS * perSecond;
  const perPicture = options.price_per_picture_usd;
  if (perPicture === null) return null;
  return durationS * perSecond + pictureCount * perPicture;
}

export function formatCost(usd: number): string {
  return money.format(usd);
}
