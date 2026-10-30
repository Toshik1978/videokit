<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useRouter } from 'vue-router';

import { getJson, postForm } from '@/api/client';
import { useMessages } from '@/i18n/messages';

import { estimateCost, formatCost } from './estimate';
import PicturePicker from './PicturePicker.vue';
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

const { t } = useI18n();
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

onMounted(async () => {
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
});

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
    await router.push(`/tools/video-generation/jobs/${encodeURIComponent(created.job_id)}`);
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
