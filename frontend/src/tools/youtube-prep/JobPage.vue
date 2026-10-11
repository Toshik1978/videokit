<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useRoute } from 'vue-router';

import { ApiError, getJson, postJson } from '@/api/client';
import { isTerminal, type JobSummary } from '@/components/job';
import JobStatus from '@/components/JobStatus.vue';
import ResultCard from '@/components/ResultCard.vue';

import RegenerateControl from './RegenerateControl.vue';
import { type RegenerateResponse, type Section, SECTIONS, type YouTubePrepResult } from './types';

const POLL_MS = 2000;

interface Job extends JobSummary {
  job_id: string;
  result: YouTubePrepResult | null;
}

const { t } = useI18n();
const route = useRoute();
const jobId = String(route.params.id);

const job = ref<Job | null>(null);
const expired = ref(false);
const error = ref('');
const cancelError = ref('');
const revisions = ref<Record<string, number>>({});
let timer: ReturnType<typeof setTimeout> | undefined;
let unmounted = false;

const result = computed(() => (job.value?.state === 'done' ? job.value.result : null));

function messageFor(err: unknown): string {
  return err instanceof ApiError && err.code !== 'http_error' ? err.message : t('errors.generic');
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
    const next = await getJson<Job>(`/api/jobs/${encodeURIComponent(jobId)}`);
    if (unmounted) return;
    error.value = '';
    applyJob(next);
    if (isTerminal(next.state)) return;
  } catch (err) {
    if (unmounted) return;
    if (err instanceof ApiError && err.status === 404) {
      expired.value = true;
      return;
    }
    error.value = messageFor(err);
  }
  timer = setTimeout(() => void poll(), POLL_MS);
}

function onRegenerated(section: Section, response: RegenerateResponse): void {
  if (!job.value) return;
  job.value = { ...job.value, result: response.result };
  revisions.value[section] = (revisions.value[section] ?? 0) + 1;
}

async function cancel(): Promise<void> {
  cancelError.value = '';
  try {
    const next = await postJson<Job>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`);
    if (unmounted) return;
    applyJob(next);
  } catch (err) {
    if (unmounted) return;
    cancelError.value = messageFor(err);
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
    <h2>{{ t('youtubePrep.heading') }}</h2>
    <p v-if="expired">{{ t('job.expired') }}</p>
    <template v-else>
      <p v-if="error || cancelError" role="alert" class="error">{{ cancelError || error }}</p>
      <JobStatus v-if="job" :job="job" @cancel="cancel" />
      <template v-if="result">
        <p v-for="notice in result.notices" :key="notice" data-test="notice" class="notice">
          {{ t(`job.notices.${notice}`) }}
        </p>
        <ResultCard
          v-for="section in SECTIONS"
          :key="section"
          :section-key="section"
          :label="t(`job.sections.${section}`)"
          :text="result.rendered[section]"
          :revision="revisions[section] ?? 0"
        >
          <template #actions>
            <RegenerateControl
              :job-id="jobId"
              :section="section"
              @regenerated="onRegenerated(section, $event)"
            />
          </template>
        </ResultCard>
      </template>
    </template>
  </section>
</template>

<style scoped>
.error {
  color: #b00020;
}

.notice {
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
}
</style>
