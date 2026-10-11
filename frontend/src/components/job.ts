export interface JobSummary {
  state: string;
  upload: { offset: number; length: number } | null;
  queue_position: number | null;
  failure_reason: string | null;
}

const TERMINAL_STATES = ['done', 'failed', 'cancelled'];

export function isTerminal(state: string): boolean {
  return TERMINAL_STATES.includes(state);
}
