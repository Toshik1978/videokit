<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useRoute } from 'vue-router';

import { ApiError, getJson, postJson } from '@/api/client';
import { isTerminal, type JobSummary } from '@/components/job';
import JobStatus from '@/components/JobStatus.vue';
import { useMessages } from '@/i18n/messages';

import ShareButton from './ShareButton.vue';
import type { AudioExtractResult } from './types';

const POLL_MS = 2000;
const TOOL_ID = 'audio_extract';

interface Job extends JobSummary {
  job_id: string;
  tool_id?: string;
  result: AudioExtractResult | null;
}

const { t } = useI18n();
const { errorMessage } = useMessages();
const route = useRoute();
const jobId = String(route.params.id);
const jobPath = `/api/jobs/${encodeURIComponent(jobId)}`;
const downloadUrl = `/api/tools/${TOOL_ID}/jobs/${encodeURIComponent(jobId)}/audio`;

const job = ref<Job | null>(null);
const expired = ref(false);
const error = ref('');
const cancelError = ref('');
let timer: ReturnType<typeof setTimeout> | undefined;
let unmounted = false;

const result = computed(() => (job.value?.state === 'done' ? job.value.result : null));

function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, '0');
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}

function formatSize(bytes: number): string {
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}

function stopTimer(): void {
  clearTimeout(timer);
  timer = undefined;
}

function applyJob(next: Job): void {
  job.value = next;
  if (isTerminal(next.state)) stopTimer();
}

async function poll(): Promise<void> {
  stopTimer();
  try {
    const next = await getJson<Job>(jobPath);
    if (unmounted) return;
    if (next.tool_id !== undefined && next.tool_id !== TOOL_ID) {
      expired.value = true;
      return;
    }
    error.value = '';
    applyJob(next);
    if (isTerminal(next.state)) return;
  } catch (err) {
    if (unmounted) return;
    if (err instanceof ApiError && err.status === 404) {
      expired.value = true;
      return;
    }
    error.value = errorMessage(err);
  }
  timer = setTimeout(() => void poll(), POLL_MS);
}

async function download(event: Event): Promise<void> {
  event.preventDefault();
  try {
    await getJson<Job>(jobPath);
  } catch (err) {
    if (unmounted) return;
    if (err instanceof ApiError && err.status === 404) {
      expired.value = true;
      return;
    }
    error.value = errorMessage(err);
    return;
  }
  if (unmounted) return;
  error.value = '';
  window.location.assign(downloadUrl);
}

async function cancel(): Promise<void> {
  cancelError.value = '';
  try {
    const next = await postJson<Job>(`${jobPath}/cancel`);
    if (unmounted) return;
    applyJob(next);
  } catch (err) {
    if (unmounted) return;
    cancelError.value = errorMessage(err);
    void poll();
  }
}

onMounted(() => void poll());
onBeforeUnmount(() => {
  unmounted = true;
  stopTimer();
});
</script>

<template>
  <section class="page">
    <h2>{{ t('audioExtract.heading') }}</h2>
    <p v-if="expired">{{ t('job.expired') }}</p>
    <template v-else>
      <p v-if="error || cancelError" role="alert" class="error">{{ cancelError || error }}</p>
      <JobStatus v-if="job" :job="job" @cancel="cancel" />
      <section v-if="result" class="result">
        <dl>
          <div>
            <dt>{{ t('audioExtract.duration') }}</dt>
            <dd data-test="duration">{{ formatDuration(result.duration_s) }}</dd>
          </div>
          <div>
            <dt>{{ t('audioExtract.size') }}</dt>
            <dd data-test="size">{{ formatSize(result.size_bytes) }}</dd>
          </div>
        </dl>
        <div class="actions">
          <a
            class="download"
            data-test="download"
            :href="downloadUrl"
            :download="result.download_name"
            @click="download"
          >
            {{ t('audioExtract.download') }}
          </a>
          <ShareButton :url="downloadUrl" :file-name="result.download_name" />
        </div>
      </section>
    </template>
  </section>
</template>

<style scoped>
.error {
  color: #b00020;
}

.result dl {
  margin: 0 0 12px;
}

.result dl div {
  display: flex;
  gap: 8px;
}

.result dt {
  color: var(--muted);
}

.result dd {
  margin: 0;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.download {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 44px;
  min-height: 44px;
  padding: 0 16px;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-weight: 600;
  text-decoration: none;
}
</style>
