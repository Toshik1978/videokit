<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useRouter } from 'vue-router';

import { getJson } from '@/api/client';
import UploadPanel from '@/components/UploadPanel.vue';
import {
  AUTO_LANGUAGE,
  endonym,
  loadChoice,
  saveChoice,
  sortedLanguages,
} from '@/tools/youtube-prep/language';

const { t } = useI18n();
const router = useRouter();

const languages = ref<string[]>([]);
const language = ref(AUTO_LANGUAGE);
const createFields = computed(() => ({ language: language.value }));

onMounted(async () => {
  try {
    const options = await getJson<{ languages: string[] }>('/api/tools/youtube_prep/options');
    languages.value = sortedLanguages(options.languages);
    language.value = loadChoice(languages.value);
  } catch {
    // Without the list only "Same as the speech" is offered.
  }
});

function onChange(): void {
  saveChoice(language.value);
}

function onUploaded(jobId: string): void {
  void router.push(`/tools/youtube-prep/jobs/${encodeURIComponent(jobId)}`);
}
</script>

<template>
  <section class="page">
    <h2>{{ t('youtubePrep.heading') }}</h2>
    <p>{{ t('youtubePrep.intro') }}</p>
    <div class="field">
      <label for="yp-language">{{ t('youtubePrep.language.label') }}</label>
      <select id="yp-language" v-model="language" data-test="language" @change="onChange">
        <option :value="AUTO_LANGUAGE">{{ t('youtubePrep.language.auto') }}</option>
        <option v-for="code in languages" :key="code" :value="code">{{ endonym(code) }}</option>
      </select>
    </div>
    <UploadPanel tool-id="youtube_prep" :create-fields="createFields" @uploaded="onUploaded" />
  </section>
</template>

<style scoped>
.field {
  display: grid;
  gap: 4px;
  margin-bottom: 16px;
}

.field label {
  font-weight: 600;
}

select {
  width: 100%;
  min-height: 44px;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg);
  color: var(--fg);
}
</style>
