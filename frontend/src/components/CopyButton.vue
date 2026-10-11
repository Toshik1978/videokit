<script setup lang="ts">
import { useI18n } from 'vue-i18n';

const props = defineProps<{ text: string }>();
const emit = defineEmits<{ copied: []; fallback: [] }>();

const { t } = useI18n();

async function copy(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.text);
    emit('copied');
  } catch {
    // Clipboard API missing (e.g. non-HTTPS) or denied: let the parent select the text instead.
    emit('fallback');
  }
}
</script>

<template>
  <button type="button" data-test="copy" @click="copy">{{ t('job.copy') }}</button>
</template>
