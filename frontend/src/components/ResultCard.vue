<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue';
import { useI18n } from 'vue-i18n';

import CopyButton from '@/components/CopyButton.vue';

const props = defineProps<{
  sectionKey: string;
  label: string;
  text: string;
  revision?: number;
  /** When set, the card shows this message instead of an editable, copyable text. */
  notice?: string;
}>();

const { t } = useI18n();
const inputId = useId();

// Edits stay in the browser (FR-030); the server copy is never modified from here.
const value = ref(props.text);
// A new `text` (or a bumped `revision`, for identical text) replaces the local copy and its edits.
watch(
  () => [props.text, props.revision],
  () => {
    value.value = props.text;
  },
);
const area = ref<HTMLTextAreaElement | null>(null);
const status = ref<'idle' | 'copied' | 'fallback'>('idle');

const rows = computed(() => Math.min(12, Math.max(3, value.value.split('\n').length + 1)));

function onFallback(): void {
  area.value?.focus();
  area.value?.select();
  status.value = 'fallback';
}
</script>

<template>
  <article class="result-card" :data-test="`card-${sectionKey}`">
    <template v-if="notice">
      <header class="card-header">
        <span class="card-label">{{ label }}</span>
      </header>
      <p class="card-notice" data-test="card-notice">{{ notice }}</p>
    </template>
    <template v-else>
      <header class="card-header">
        <label :for="inputId" class="card-label">{{ label }}</label>
        <div class="card-actions">
          <CopyButton :text="value" @copied="status = 'copied'" @fallback="onFallback" />
          <slot name="actions" />
        </div>
      </header>
      <textarea :id="inputId" ref="area" v-model="value" :rows="rows" @input="status = 'idle'" />
      <p v-if="status === 'copied'" role="status" class="hint">{{ t('job.copied') }}</p>
      <p v-else-if="status === 'fallback'" role="status" class="hint">{{ t('job.pressCopy') }}</p>
    </template>
  </article>
</template>

<style scoped>
.result-card {
  margin-bottom: var(--gutter);
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}

.card-label {
  font-weight: 600;
}

.card-notice {
  margin: 8px 0 0;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
}

.card-actions {
  display: flex;
  gap: 8px;
}

textarea {
  display: block;
  width: 100%;
  max-width: 100%;
  margin-top: 8px;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg);
  color: var(--fg);
  resize: vertical;
}

.hint {
  margin: 4px 0 0;
  color: var(--muted);
}
</style>
