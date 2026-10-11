<script setup lang="ts">
import { Upload } from 'tus-js-client';
import { computed, onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';

import { ApiError, getJson, postJson } from '@/api/client';

const props = defineProps<{ toolId: string }>();
const emit = defineEmits<{ uploaded: [jobId: string] }>();

interface AppConfig {
  max_upload_bytes: number;
  upload_chunk_bytes: number;
}

const RETRY_DELAYS = [0, 1000, 3000, 5000, 10000];
const UNITS = ['B', 'KB', 'MB', 'GB', 'TB'];

const { t } = useI18n();

const config = ref<AppConfig | null>(null);
const busy = ref(false);
const percent = ref(0);
const error = ref('');

const progressLabel = computed(() => t('upload.progress', { percent: percent.value }));

function formatBytes(bytes: number): string {
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${Number(value.toFixed(1))} ${UNITS[unit]}`;
}

function limitMessage(): string {
  return t('upload.tooLarge', { limit: formatBytes(config.value?.max_upload_bytes ?? 0) });
}

function messageFor(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'file_too_large') return limitMessage();
    if (err.code !== 'http_error') return err.message;
  }
  return t('errors.generic');
}

onMounted(async () => {
  try {
    config.value = await getJson<AppConfig>('/api/config');
  } catch (err) {
    error.value = messageFor(err);
  }
});

function startTus(file: File, uploadUrl: string, jobId: string, chunkSize: number): void {
  new Upload(file, {
    uploadUrl,
    chunkSize,
    retryDelays: RETRY_DELAYS,
    onProgress: (sent, total) => {
      percent.value = total > 0 ? Math.floor((sent / total) * 100) : 0;
    },
    onSuccess: () => emit('uploaded', jobId),
    onError: () => {
      busy.value = false;
      error.value = t('upload.failed');
    },
  }).start();
}

async function onChange(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  // Clear so re-selecting the same file (e.g. to retry) fires `change` again.
  input.value = '';
  if (!file || !config.value) return;
  error.value = '';
  if (file.size > config.value.max_upload_bytes) {
    error.value = limitMessage();
    return;
  }
  busy.value = true;
  percent.value = 0;
  try {
    const job = await postJson<{ job_id: string; upload_url: string }>('/api/jobs', {
      tool_id: props.toolId,
      size: file.size,
    });
    startTus(file, job.upload_url, job.job_id, config.value.upload_chunk_bytes);
  } catch (err) {
    busy.value = false;
    error.value = messageFor(err);
  }
}
</script>

<template>
  <div class="upload-panel">
    <label class="file-label">
      <span>{{ t('upload.choose') }}</span>
      <input type="file" accept="video/*" :disabled="busy || !config" @change="onChange" />
    </label>
    <p v-if="config" class="hint">
      {{ t('upload.limit', { limit: formatBytes(config.max_upload_bytes) }) }}
    </p>
    <div v-if="busy" class="upload-progress">
      <progress max="100" :value="percent" :aria-label="progressLabel"></progress>
      <span>{{ progressLabel }}</span>
    </div>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
  </div>
</template>

<style scoped>
.file-label {
  min-width: var(--touch);
  min-height: var(--touch);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.file-label input {
  min-width: var(--touch);
  min-height: var(--touch);
  width: 100%;
  max-width: 100%;
}

.hint {
  color: var(--muted);
}

.upload-progress {
  display: flex;
  align-items: center;
  gap: 8px;
}

.upload-progress progress {
  flex: 1;
  min-width: 0;
  height: var(--touch);
}

.error {
  color: #b00020;
}
</style>
