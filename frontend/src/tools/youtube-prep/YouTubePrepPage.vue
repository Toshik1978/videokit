<script setup lang="ts">
import { useI18n } from 'vue-i18n';
import { useRouter } from 'vue-router';

import UploadPanel from '@/components/UploadPanel.vue';

const { t } = useI18n();
const router = useRouter();

function onUploaded(jobId: string): void {
  void router.push(`/tools/youtube-prep/jobs/${encodeURIComponent(jobId)}`);
}
</script>

<template>
  <section class="page">
    <h2>{{ t('youtubePrep.heading') }}</h2>
    <p>{{ t('youtubePrep.intro') }}</p>
    <UploadPanel tool-id="youtube_prep" @uploaded="onUploaded" />
  </section>
</template>
