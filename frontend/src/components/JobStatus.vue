<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';

import type { JobSummary } from '@/components/job';
import { isTerminal } from '@/components/job';

const props = defineProps<{ job: JobSummary }>();
defineEmits<{ cancel: [] }>();

const { t } = useI18n();

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
    <p v-if="job.state === 'failed' && job.failure_reason" class="error">
      {{ t('job.failureReason', { reason: job.failure_reason }) }}
    </p>
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
</style>
