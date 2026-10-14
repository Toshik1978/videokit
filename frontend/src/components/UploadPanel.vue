<script setup lang="ts">
import { Upload } from 'tus-js-client';
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';

import { ApiError, getJson, postJson } from '@/api/client';
import { useMessages } from '@/i18n/messages';

const props = defineProps<{ toolId: string }>();
const emit = defineEmits<{ uploaded: [jobId: string] }>();

interface AppConfig {
  max_upload_bytes: number;
  upload_chunk_bytes: number;
}

const RETRY_DELAYS = [0, 1000, 3000, 5000, 10000];
const UNITS = ['B', 'KB', 'MB', 'GB', 'TB'];

const { t } = useI18n();
const { errorCodeMessage, errorMessage, hasErrorCode } = useMessages();

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
  return err instanceof ApiError && err.code === 'file_too_large'
    ? limitMessage()
    : errorMessage(err);
}

/** The API error code in a failed tus response body, if any (tus `DetailedError`). */
function tusErrorCode(err: unknown): string | null {
  const response = (err as { originalResponse?: { getBody?: () => unknown } | null } | null)
    ?.originalResponse;
  try {
    const body = JSON.parse(String(response?.getBody?.())) as { error?: { code?: unknown } };
    return typeof body.error?.code === 'string' ? body.error.code : null;
  } catch {
    return null;
  }
}

function uploadErrorMessage(err: unknown): string {
  const code = tusErrorCode(err);
  return code && hasErrorCode(code) ? errorCodeMessage(code) : t('upload.failed');
}

onMounted(async () => {
  try {
    config.value = await getJson<AppConfig>('/api/config');
  } catch (err) {
    error.value = messageFor(err);
  }
});

const STORAGE_KEY = 'videokit.uploads';

interface StoredUpload {
  job_id: string;
  upload_url: string;
}

// Resume state is kept in our own localStorage entry (fingerprint -> job_id, upload_url);
// tus-js-client's built-in fingerprint/url storage is disabled so there is a single source of truth.
// The fingerprint starts with the tool id: the same file picked on another tool's page is a new job,
// and that tool's saved upload stays untouched (its page may still resume it).
function readStore(): Record<string, StoredUpload> {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}');
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, StoredUpload>) : {};
  } catch {
    return {};
  }
}

function writeStore(store: Record<string, StoredUpload>): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
  } catch {
    // Storage unavailable: uploads still work, they just cannot be resumed after a reload.
  }
}

function saveEntry(fingerprint: string, entry: StoredUpload): void {
  writeStore({ ...readStore(), [fingerprint]: entry });
}

function clearEntry(fingerprint: string): void {
  const store = readStore();
  delete store[fingerprint];
  writeStore(store);
}

function fingerprintOf(file: File): string {
  return [props.toolId, file.name, file.size, file.lastModified, file.type].join('|');
}

let current: { upload: Upload; fingerprint: string; jobId: string } | null = null;

function startTus(file: File, entry: StoredUpload, fingerprint: string, chunkSize: number): void {
  const upload = new Upload(file, {
    uploadUrl: entry.upload_url,
    chunkSize,
    retryDelays: RETRY_DELAYS,
    storeFingerprintForResuming: false,
    onProgress: (sent, total) => {
      percent.value = total > 0 ? Math.floor((sent / total) * 100) : 0;
    },
    onSuccess: () => {
      clearEntry(fingerprint);
      current = null;
      emit('uploaded', entry.job_id);
    },
    onError: (err) => {
      busy.value = false;
      current = null;
      error.value = uploadErrorMessage(err);
    },
  });
  current = { upload, fingerprint, jobId: entry.job_id };
  paused = false;
  upload.start();
}

// Only restart an upload that `offline` paused; `online` also fires on network switches.
let paused = false;

function pause(): void {
  if (!current) return;
  paused = true;
  void current.upload.abort();
}

function unpause(): void {
  if (!current || !paused) return;
  paused = false;
  current.upload.start();
}

onMounted(() => {
  window.addEventListener('offline', pause);
  window.addEventListener('online', unpause);
});
onBeforeUnmount(() => {
  window.removeEventListener('offline', pause);
  window.removeEventListener('online', unpause);
});

async function findResumable(fingerprint: string): Promise<StoredUpload | null> {
  const entry = readStore()[fingerprint];
  if (!entry) return null;
  try {
    const job = await getJson<{ tool_id?: string; state: string }>(
      `/api/jobs/${encodeURIComponent(entry.job_id)}`,
    );
    // Another tool's job is never resumed (nor cancelled); the entry is replaced by a fresh job.
    if (job.tool_id !== props.toolId) return null;
    if (job.state === 'uploading') return entry;
  } catch {
    // Job gone or unreachable: fall through and start fresh.
  }
  clearEntry(fingerprint);
  return null;
}

async function cancelJob(jobId: string): Promise<void> {
  try {
    await postJson(`/api/jobs/${encodeURIComponent(jobId)}/cancel`);
  } catch {
    // Best effort: the server expires abandoned uploads on its own.
  }
}

// The selection currently being prepared (before tus starts); cancel marks it so it never starts.
let pending: { cancelled: boolean } | null = null;

async function cancelUpload(): Promise<void> {
  const active = current;
  if (!active) {
    if (pending) pending.cancelled = true;
    busy.value = false;
    return;
  }
  current = null;
  void active.upload.abort();
  clearEntry(active.fingerprint);
  busy.value = false;
  await cancelJob(active.jobId);
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
  const fingerprint = fingerprintOf(file);
  const attempt = { cancelled: false };
  pending = attempt;
  try {
    let entry = await findResumable(fingerprint);
    if (attempt.cancelled) {
      if (entry) {
        clearEntry(fingerprint);
        await cancelJob(entry.job_id);
      }
      return;
    }
    if (!entry) {
      const job = await postJson<{ job_id: string; upload_url: string }>('/api/jobs', {
        tool_id: props.toolId,
        size: file.size,
        file_name: file.name,
      });
      if (attempt.cancelled) {
        await cancelJob(job.job_id);
        return;
      }
      entry = { job_id: job.job_id, upload_url: job.upload_url };
      saveEntry(fingerprint, entry);
    }
    startTus(file, entry, fingerprint, config.value.upload_chunk_bytes);
  } catch (err) {
    if (attempt.cancelled) return;
    busy.value = false;
    error.value = messageFor(err);
  } finally {
    if (pending === attempt) pending = null;
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
      <button type="button" data-test="cancel-upload" @click="cancelUpload">
        {{ t('upload.cancel') }}
      </button>
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

.upload-progress button {
  min-width: var(--touch);
  min-height: var(--touch);
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
