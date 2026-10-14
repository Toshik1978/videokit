<script setup lang="ts">
import { useI18n } from 'vue-i18n';
import { useRouter } from 'vue-router';

import UploadPanel from '@/components/UploadPanel.vue';

const { t } = useI18n();
const router = useRouter();

function onUploaded(jobId: string): void {
  void router.push(`/tools/audio-extract/jobs/${encodeURIComponent(jobId)}`);
}
</script>

<template>
  <section class="page">
    <h2>{{ t('audioExtract.heading') }}</h2>
    <p>{{ t('audioExtract.intro') }}</p>
    <UploadPanel tool-id="audio_extract" @uploaded="onUploaded" />
  </section>
</template>
