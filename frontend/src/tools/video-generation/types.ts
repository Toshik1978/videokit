/** `GET /api/tools/video_generation/options`: what the form offers and what its estimate needs. */
export interface VideoOptions {
  durations_s: number[];
  default_duration_s: number;
  aspect_ratios: string[];
  resolutions: string[];
  max_pictures: number;
  max_picture_bytes: number;
  picture_types: string[];
  max_prompt_chars: number;
  /** USD per second of clip per resolution; null when the model has no known price. */
  price_per_second_usd: Record<string, number> | null;
  /** USD per input picture; null when the model has no known price. */
  price_per_picture_usd: number | null;
}

/** `POST /api/tools/video_generation/jobs` answer. */
export interface CreatedJob {
  job_id: string;
}

/** `result` of a finished Video Generation job (`GET /api/jobs/{id}`). */
export interface VideoResult {
  file: string;
  thumbnail: string;
  download_name: string;
  duration_s: number;
  width: number;
  height: number;
  aspect_ratio: string;
  resolution: string;
  size_bytes: number;
}
