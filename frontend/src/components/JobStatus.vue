<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';

import type { JobSummary } from '@/components/job';
import { isTerminal } from '@/components/job';
import { useMessages } from '@/i18n/messages';

const props = defineProps<{ job: JobSummary }>();
defineEmits<{ cancel: [] }>();

const { t } = useI18n();
const { failureMessage } = useMessages();

const terminal = computed(() => isTerminal(props.job.state));
const uploadPercent = computed(() => {
  const upload = props.job.upload;
  return upload && upload.length > 0 ? Math.floor((upload.offset / upload.length) * 100) : 0;
});
</script>

<template>
  <section class="job-status" aria-live="polite">
    <p class="stage">{{ t(`job.state.${job.state}`) }}</p>
    <p v-if="job.state === 'uploading'">{{ t('job.uploadPercent', { percent: uploadPercent }) }}</p>
    <p v-if="job.state === 'queued' && job.queue_position !== null">
      {{ t('job.queuePosition', { position: job.queue_position }) }}
    </p>
    <template v-if="job.state === 'failed'">
      <p class="error" data-test="failure">{{ failureMessage(job.failure_code) }}</p>
      <p v-if="job.failure_reason" class="detail" data-test="failure-detail">
        {{ t('job.failureDetail', { detail: job.failure_reason }) }}
      </p>
    </template>
    <button v-if="!terminal" type="button" data-test="cancel" @click="$emit('cancel')">
      {{ t('job.cancel') }}
    </button>
  </section>
</template>

<style scoped>
.stage {
  font-weight: 600;
}

.error {
  color: #b00020;
}

.detail {
  color: var(--muted);
}
</style>
