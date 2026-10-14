<script setup lang="ts">
import { computed, ref } from 'vue';
import { useI18n } from 'vue-i18n';

import { useMessages } from '@/i18n/messages';

const props = defineProps<{ url: string; fileName: string }>();

const MIME = 'audio/mpeg';

const { t } = useI18n();
const { errorMessage } = useMessages();

const supported =
  typeof navigator !== 'undefined' &&
  typeof navigator.canShare === 'function' &&
  navigator.canShare({ files: [new File([], 'a.mp3', { type: MIME })] });

const preparing = ref(false);
const busy = ref(false); // covers both the fetch and the open share sheet
const ready = ref(false);
const error = ref('');
let file: File | null = null; // cached for the page's lifetime only

const label = computed(() => {
  if (preparing.value) return t('audioExtract.sharePreparing');
  return ready.value ? t('audioExtract.shareReady') : t('audioExtract.share');
});

async function fetchFile(): Promise<File> {
  const response = await fetch(props.url);
  if (!response.ok) throw new Error('fetch failed');
  return new File([await response.blob()], props.fileName, { type: MIME });
}

async function share(): Promise<void> {
  if (busy.value) return;
  busy.value = true;
  error.value = '';
  try {
    if (file === null) {
      preparing.value = true;
      try {
        file = await fetchFile();
      } finally {
        preparing.value = false;
      }
    }
    await navigator.share({ files: [file] });
    ready.value = false;
  } catch (err) {
    const name = err instanceof Error ? err.name : '';
    if (name === 'AbortError') {
      ready.value = false;
    } else if (name === 'NotAllowedError' && file !== null) {
      ready.value = true;
    } else {
      ready.value = false;
      error.value = errorMessage(err);
    }
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <template v-if="supported">
    <button type="button" class="share" data-test="share" :disabled="busy" @click="share">
      {{ label }}
    </button>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
  </template>
</template>

<style scoped>
.share {
  min-width: 44px;
  min-height: 44px;
  padding: 0 16px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: transparent;
  color: inherit;
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}

.error {
  flex-basis: 100%;
  margin: 0;
  color: #b00020;
}
</style>
