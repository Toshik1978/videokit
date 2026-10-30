<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { useRoute } from 'vue-router';

import { ApiError, getJson, postJson } from '@/api/client';
import { isTerminal, type JobSummary, type Usage } from '@/components/job';
import JobStatus from '@/components/JobStatus.vue';
import ShareButton from '@/components/ShareButton.vue';
import { useMessages } from '@/i18n/messages';
import NotFoundPage from '@/pages/NotFoundPage.vue';

import { isToolHidden } from './availability';
import { formatCost } from './estimate';
import type { JobInputs, VideoResult } from './types';

const POLL_MS = 2000;
const TOOL_ID = 'video_generation';

interface Job extends JobSummary {
  job_id: string;
  tool_id?: string;
  usage?: Usage | null;
  result: VideoResult | null;
}

const { t, te } = useI18n();
const { errorMessage } = useMessages();
const route = useRoute();
const jobId = String(route.params.id);
const jobPath = `/api/jobs/${encodeURIComponent(jobId)}`;
const toolPath = `/api/tools/${TOOL_ID}/jobs/${encodeURIComponent(jobId)}`;
const clipUrl = `${toolPath}/clip`;
const thumbnailUrl = `${toolPath}/thumbnail`;
const inputsUrl = `${toolPath}/inputs`;
const tryAgainTo = `/tools/video-generation?from=${encodeURIComponent(jobId)}`;

const job = ref<Job | null>(null);
const expired = ref(false);
/** Not configured on this server: the tool is not listed. */
const hidden = ref(false);
const checked = ref(false);
const error = ref('');
const cancelError = ref('');
const confirmingCancel = ref(false);
const canTryAgain = ref(false);
let timer: ReturnType<typeof setTimeout> | undefined;
let unmounted = false;

const stageLabels = computed(() => ({ generating: t('videoGeneration.stage.generating') }));
const result = computed(() => (job.value?.state === 'done' ? job.value.result : null));

const durationText = computed(() =>
  // The measured length is unrounded (e.g. 8.041667); whole seconds match the 5-10 s choices.
  result.value
    ? t('videoGeneration.options.seconds', { seconds: Math.round(result.value.duration_s) })
    : '',
);
const orientationText = computed(() =>
  result.value?.aspect_ratio === '16:9'
    ? t('videoGeneration.options.horizontal')
    : t('videoGeneration.options.vertical'),
);
const resolutionText = computed(() => {
  const resolution = result.value?.resolution ?? '';
  const key = `videoGeneration.options.p${resolution}`;
  return te(key) ? t(key) : resolution;
});
const costText = computed(() => {
  const cost = job.value?.usage?.cost_usd;
  return cost === null || cost === undefined ? t('usage.notAvailable') : formatCost(cost);
});

function stopTimer(): void {
  clearTimeout(timer);
  timer = undefined;
}

/**
 * Try again needs the job's stored inputs: the server answers 200 while they exist and 404 once the
 * job expired or was cancelled (a cancel deletes the job's data). Any other answer hides the button.
 */
async function probeInputs(): Promise<void> {
  try {
    const inputs = await getJson<JobInputs>(inputsUrl);
    if (!unmounted) canTryAgain.value = typeof inputs.prompt === 'string';
  } catch {
    if (!unmounted) canTryAgain.value = false;
  }
}

function applyJob(next: Job): void {
  const wasTerminal = job.value !== null && isTerminal(job.value.state);
  job.value = next;
  if (!isTerminal(next.state)) return;
  stopTimer();
  if (!wasTerminal) void probeInputs();
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
  window.location.assign(clipUrl);
}

async function cancel(): Promise<void> {
  confirmingCancel.value = false;
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

// A generating job may already be billed: ask first. A queued job costs nothing to cancel.
function requestCancel(): void {
  if (job.value?.state === 'generating') confirmingCancel.value = true;
  else void cancel();
}

// The warning only makes sense while the job is still generating.
watch(
  () => job.value?.state,
  (state) => {
    if (state !== 'generating') confirmingCancel.value = false;
  },
);

onMounted(async () => {
  hidden.value = await isToolHidden(TOOL_ID);
  checked.value = true;
  if (!hidden.value && !unmounted) void poll();
});
onBeforeUnmount(() => {
  unmounted = true;
  stopTimer();
});
</script>

<template>
  <NotFoundPage v-if="hidden" />
  <section v-else-if="checked" class="page">
    <h2>{{ t('videoGeneration.stage.heading') }}</h2>
    <p v-if="expired">{{ t('videoGeneration.expired') }}</p>
    <template v-else>
      <p v-if="error || cancelError" role="alert" class="error">{{ cancelError || error }}</p>
      <JobStatus
        v-if="job"
        :job="job"
        :stage-labels="stageLabels"
        :show-detail="false"
        @cancel="requestCancel"
      />
      <div v-if="confirmingCancel" class="warning" role="alert" data-test="cancel-warning">
        <p>{{ t('videoGeneration.cancelWarning') }}</p>
        <div class="actions">
          <button type="button" data-test="cancel-confirm" @click="cancel">
            {{ t('videoGeneration.cancelConfirm') }}
          </button>
          <button type="button" data-test="cancel-keep" @click="confirmingCancel = false">
            {{ t('videoGeneration.cancelKeep') }}
          </button>
        </div>
      </div>
      <section v-if="result" class="result">
        <video
          class="preview"
          :src="clipUrl"
          :poster="thumbnailUrl"
          :aria-label="t('videoGeneration.result.preview')"
          playsinline
          muted
          controls
          preload="metadata"
        ></video>
        <dl>
          <div>
            <dt>{{ t('videoGeneration.result.duration') }}</dt>
            <dd data-test="duration">{{ durationText }}</dd>
          </div>
          <div>
            <dt>{{ t('videoGeneration.result.orientation') }}</dt>
            <dd data-test="orientation">{{ orientationText }}</dd>
          </div>
          <div>
            <dt>{{ t('videoGeneration.result.resolution') }}</dt>
            <dd data-test="resolution">{{ resolutionText }}</dd>
          </div>
          <div>
            <dt>{{ t('videoGeneration.result.cost') }}</dt>
            <dd data-test="cost">{{ costText }}</dd>
          </div>
        </dl>
        <div class="actions">
          <a
            class="download"
            data-test="download"
            :href="clipUrl"
            :download="result.download_name"
            @click="download"
          >
            {{ t('videoGeneration.result.download') }}
          </a>
          <ShareButton
            :file-url="clipUrl"
            :file-name="result.download_name"
            mime-type="video/mp4"
          />
        </div>
      </section>
      <div v-if="canTryAgain" class="actions again">
        <RouterLink class="download" data-test="try-again" :to="tryAgainTo">
          {{ t('videoGeneration.result.tryAgain') }}
        </RouterLink>
      </div>
    </template>
  </section>
</template>

<style scoped>
.error {
  color: #b00020;
}

.warning {
  margin: 12px 0;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
}

.warning p {
  margin: 0 0 8px;
}

.warning button {
  min-width: 44px;
  min-height: 44px;
  padding: 0 16px;
  font: inherit;
}

.preview {
  display: block;
  width: 100%;
  max-width: 100%;
  max-height: 70vh;
  margin: 0 0 12px;
  border-radius: 8px;
  background: #000;
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

.again {
  margin: 12px 0;
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
