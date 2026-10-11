import type { Usage } from '@/components/job';

export const SECTIONS = [
  'cover_titles',
  'title',
  'description',
  'tags',
  'hashtags',
  'chapters',
] as const;

export type Section = (typeof SECTIONS)[number];

export interface YouTubePrepResult {
  notices: string[];
  rendered: Record<Section, string>;
}

export interface RegenerateResponse {
  result: YouTubePrepResult;
  usage?: Partial<Usage>;
}
