export interface JobSummary {
  state: string;
  upload: { offset: number; length: number } | null;
  queue_position: number | null;
  failure_reason: string | null;
}

export interface Usage {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  audio_minutes: number;
  cost_usd: number | null;
}

const TERMINAL_STATES = ['done', 'failed', 'cancelled'];

export function isTerminal(state: string): boolean {
  return TERMINAL_STATES.includes(state);
}
