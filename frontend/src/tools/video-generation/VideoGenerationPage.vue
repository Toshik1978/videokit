<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useRouter } from 'vue-router';

import { ApiError, getJson, postForm } from '@/api/client';
import { useMessages } from '@/i18n/messages';

import { estimateCost, formatCost } from './estimate';
import PicturePicker from './PicturePicker.vue';
import { addRecentClip, listRecentClips, removeRecentClip } from './recentClips';
import type { CreatedJob, VideoOptions } from './types';

const DEFAULT_ASPECT = '9:16';
const DEFAULT_RESOLUTION = '1080p';
const ASPECT_LABELS: Record<string, string> = {
  '9:16': 'videoGeneration.options.vertical',
  '16:9': 'videoGeneration.options.horizontal',
};
const RESOLUTION_LABELS: Record<string, string> = {
  '1080p': 'videoGeneration.options.p1080',
  '720p': 'videoGeneration.options.p720',
};

/** A listed clip with what the server says about its job right now (`state` is null if unknown). */
interface ListedClip {
  job_id: string;
  prompt: string;
  state: string | null;
}

const { t, te } = useI18n();
const { errorMessage } = useMessages();
const router = useRouter();

const options = ref<VideoOptions | null>(null);
const loadError = ref('');
const submitError = ref('');
const submitting = ref(false);

const prompt = ref('');
const duration = ref(0);
const aspect = ref(DEFAULT_ASPECT);
const resolution = ref(DEFAULT_RESOLUTION);
const pictures = ref<File[]>([]);

const jobTtlHours = ref<number | null>(null);
const recent = ref<ListedClip[]>([]);
const recentLoaded = ref(false);

const canSubmit = computed(() => prompt.value.trim() !== '' && !submitting.value);

const estimate = computed(() => {
  if (options.value === null) return '';
  const cost = estimateCost(options.value, duration.value, resolution.value, pictures.value.length);
  return cost === null
    ? t('videoGeneration.estimateUnknown')
    : t('videoGeneration.estimate', { cost: formatCost(cost) });
});

function labelFor(labels: Record<string, string>, value: string): string {
  const key = labels[value];
  return key === undefined ? value : t(key);
}

function preferred(values: string[], wanted: string): string {
  return values.includes(wanted) ? wanted : (values[0] ?? wanted);
}

async function loadTtlHours(): Promise<number | null> {
  try {
    const config = await getJson<{ job_ttl_hours: number }>('/api/config');
    return typeof config.job_ttl_hours === 'number' ? config.job_ttl_hours : null;
  } catch {
    return null;
  }
}

/** The job's state; null when the server cannot say right now; undefined when the job is gone. */
async function jobState(jobId: string): Promise<string | null | undefined> {
  try {
    const job = await getJson<{ state: string }>(`/api/jobs/${encodeURIComponent(jobId)}`);
    return job.state;
  } catch (err) {
    return err instanceof ApiError && err.status === 404 && err.code === 'job_not_found'
      ? undefined
      : null;
  }
}

async function loadRecent(): Promise<void> {
  const hours = await loadTtlHours();
  jobTtlHours.value = hours;
  if (hours !== null) {
    const clips = listRecentClips(hours);
    const states = await Promise.all(clips.map((clip) => jobState(clip.job_id)));
    const listed: ListedClip[] = [];
    clips.forEach((clip, index) => {
      const state = states[index];
      if (state === undefined) removeRecentClip(clip.job_id);
      else listed.push({ job_id: clip.job_id, prompt: clip.prompt, state });
    });
    recent.value = listed;
  }
  recentLoaded.value = true;
}

function statusLabel(state: string | null): string {
  const key = `videoGeneration.recent.status.${state}`;
  return state !== null && te(key) ? t(key) : '';
}

function thumbnailUrl(jobId: string): string {
  return `/api/tools/video_generation/jobs/${encodeURIComponent(jobId)}/thumbnail`;
}

function jobRoute(jobId: string): string {
  return `/tools/video-generation/jobs/${encodeURIComponent(jobId)}`;
}

onMounted(() => {
  void loadRecent();
  void loadOptions();
});

async function loadOptions(): Promise<void> {
  try {
    const loaded = await getJson<VideoOptions>('/api/tools/video_generation/options');
    duration.value = loaded.durations_s.includes(loaded.default_duration_s)
      ? loaded.default_duration_s
      : (loaded.durations_s[0] ?? loaded.default_duration_s);
    aspect.value = preferred(loaded.aspect_ratios, DEFAULT_ASPECT);
    resolution.value = preferred(loaded.resolutions, DEFAULT_RESOLUTION);
    options.value = loaded;
  } catch (err) {
    loadError.value = errorMessage(err);
  }
}

/** Lists the new job on this device. Without the retention period it stays unlisted. */
async function remember(jobId: string, text: string): Promise<void> {
  const hours = jobTtlHours.value ?? (await loadTtlHours());
  if (hours === null) return;
  addRecentClip({ job_id: jobId, created_at: new Date().toISOString(), prompt: text }, hours);
}

async function submit(): Promise<void> {
  if (!canSubmit.value) return;
  submitting.value = true;
  submitError.value = '';
  const form = new FormData();
  form.append('prompt', prompt.value);
  form.append('duration_s', String(duration.value));
  form.append('aspect_ratio', aspect.value);
  form.append('resolution', resolution.value);
  for (const picture of pictures.value) form.append('pictures', picture, picture.name);
  try {
    const created = await postForm<CreatedJob>('/api/tools/video_generation/jobs', form);
    await remember(created.job_id, prompt.value);
    await router.push(jobRoute(created.job_id));
  } catch (err) {
    submitError.value = errorMessage(err);
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <section class="page">
    <h2>{{ t('videoGeneration.heading') }}</h2>
    <p>{{ t('videoGeneration.intro') }}</p>
    <section v-if="recentLoaded && jobTtlHours !== null" class="recent" aria-labelledby="vg-recent">
      <h3 id="vg-recent" data-test="recent-heading">{{ t('videoGeneration.recent.heading') }}</h3>
      <ul v-if="recent.length > 0" class="clips">
        <li v-for="clip in recent" :key="clip.job_id">
          <RouterLink class="clip" :to="jobRoute(clip.job_id)" data-test="recent-clip">
            <img
              v-if="clip.state === 'done'"
              class="thumb"
              :src="thumbnailUrl(clip.job_id)"
              :alt="t('videoGeneration.recent.thumbnail')"
            />
            <span class="clip-text">
              <span class="clip-prompt">{{ clip.prompt }}</span>
              <span v-if="statusLabel(clip.state)" class="clip-status" data-test="recent-status">
                {{ statusLabel(clip.state) }}
              </span>
            </span>
          </RouterLink>
        </li>
      </ul>
      <p v-else class="muted" data-test="recent-empty">{{ t('videoGeneration.recent.empty') }}</p>
      <p class="muted" data-test="retention">
        {{ t('videoGeneration.retention', { hours: jobTtlHours }) }}
      </p>
    </section>
    <p v-if="loadError" role="alert" class="error">{{ loadError }}</p>
    <form v-else-if="options" class="form" @submit.prevent="submit">
      <div class="field">
        <label for="vg-prompt">{{ t('videoGeneration.prompt.label') }}</label>
        <textarea
          id="vg-prompt"
          v-model="prompt"
          rows="5"
          :maxlength="options.max_prompt_chars"
          :placeholder="t('videoGeneration.prompt.placeholder')"
          data-test="prompt"
        ></textarea>
        <p class="count" data-test="prompt-count">
          {{
            t('videoGeneration.prompt.count', {
              count: prompt.length,
              max: options.max_prompt_chars,
            })
          }}
        </p>
      </div>

      <PicturePicker v-model="pictures" :limits="options" />

      <div class="field">
        <label for="vg-duration">{{ t('videoGeneration.options.duration') }}</label>
        <select id="vg-duration" v-model.number="duration" data-test="duration">
          <option v-for="seconds in options.durations_s" :key="seconds" :value="seconds">
            {{ t('videoGeneration.options.seconds', { seconds }) }}
          </option>
        </select>
      </div>

      <fieldset class="choice">
        <legend>{{ t('videoGeneration.options.orientation') }}</legend>
        <label v-for="ratio in options.aspect_ratios" :key="ratio" class="segment">
          <input
            v-model="aspect"
            type="radio"
            name="aspect"
            :value="ratio"
            :data-test="`aspect-${ratio}`"
          />
          <span>{{ labelFor(ASPECT_LABELS, ratio) }}</span>
        </label>
      </fieldset>

      <fieldset class="choice">
        <legend>{{ t('videoGeneration.options.resolution') }}</legend>
        <label v-for="value in options.resolutions" :key="value" class="segment">
          <input
            v-model="resolution"
            type="radio"
            name="resolution"
            :value="value"
            :data-test="`resolution-${value}`"
          />
          <span>{{ labelFor(RESOLUTION_LABELS, value) }}</span>
        </label>
      </fieldset>

      <p class="estimate" data-test="estimate">{{ estimate }}</p>
      <p v-if="submitError" role="alert" class="error" data-test="submit-error">
        {{ submitError }}
      </p>
      <button type="submit" :disabled="!canSubmit" data-test="generate">
        {{ submitting ? t('videoGeneration.submitting') : t('videoGeneration.generate') }}
      </button>
    </form>
  </section>
</template>

<style scoped>
.recent {
  margin-bottom: 24px;
}

.recent h3 {
  margin: 0 0 8px;
}

.muted {
  margin: 8px 0 0;
  color: var(--muted);
}

.clips {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.clip {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: var(--touch);
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  color: inherit;
  text-decoration: none;
}

.thumb {
  flex: 0 0 auto;
  width: 48px;
  height: 48px;
  border-radius: 4px;
  object-fit: cover;
}

.clip-text {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.clip-prompt {
  overflow-wrap: anywhere;
}

.clip-status {
  color: var(--muted);
  font-size: 0.875rem;
}

.form {
  display: grid;
  gap: 16px;
}

.field {
  display: grid;
  gap: 4px;
}

.field label,
.choice legend {
  font-weight: 600;
}

textarea,
select {
  width: 100%;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg);
  color: var(--fg);
}

textarea {
  resize: vertical;
}

.count {
  margin: 0;
  color: var(--muted);
  text-align: right;
}

.choice {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0;
  padding: 0;
  border: 0;
  min-width: 0;
}

.choice legend {
  flex: 0 0 100%;
  padding: 0;
  margin-bottom: 4px;
}

/* The label is the touch target; the radio is visually hidden but stays focusable. */
.segment {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: var(--touch);
  min-height: var(--touch);
  padding: 0 var(--gutter);
  border: 1px solid var(--border);
  border-radius: 8px;
  cursor: pointer;
}

.segment input {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  margin: 0;
  opacity: 0;
  cursor: pointer;
}

.segment:has(input:checked) {
  border-color: var(--accent);
  background: var(--accent);
  color: var(--accent-fg);
}

.segment:has(input:focus-visible) {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.estimate {
  margin: 0;
  font-weight: 600;
}

button[type='submit']:disabled {
  opacity: 0.5;
  cursor: default;
}

.error {
  color: #b00020;
}
</style>
