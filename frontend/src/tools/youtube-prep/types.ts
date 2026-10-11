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

export interface Usage {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  audio_minutes: number;
  cost_usd: number | null;
}

export interface RegenerateResponse {
  result: YouTubePrepResult;
  usage?: Partial<Usage>;
}
