<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';

import type { Usage } from '@/components/job';

const props = defineProps<{
  usage: Usage;
  providers?: { transcription?: string; llm?: string } | null;
}>();

const { t } = useI18n();

const number = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 });
const money = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});

const cost = computed(() =>
  props.usage.cost_usd === null ? t('usage.notAvailable') : money.format(props.usage.cost_usd),
);
</script>

<template>
  <footer class="usage-footer" data-test="usage-footer">
    <h3>{{ t('usage.heading') }}</h3>
    <ul>
      <li v-if="providers?.transcription">
        {{ t('usage.transcription', { name: providers.transcription }) }}
      </li>
      <li v-if="providers?.llm">{{ t('usage.llm', { name: providers.llm }) }}</li>
      <li>{{ t('usage.inputTokens', { count: number.format(usage.input_tokens) }) }}</li>
      <li>{{ t('usage.outputTokens', { count: number.format(usage.output_tokens) }) }}</li>
      <li>
        {{
          t('usage.cacheTokens', {
            read: number.format(usage.cache_read_tokens),
            written: number.format(usage.cache_write_tokens),
          })
        }}
      </li>
      <li>{{ t('usage.audioMinutes', { minutes: number.format(usage.audio_minutes) }) }}</li>
      <li data-test="usage-cost">{{ t('usage.cost', { cost }) }}</li>
    </ul>
  </footer>
</template>

<style scoped>
.usage-footer {
  margin-top: var(--gutter);
  padding-top: 8px;
  border-top: 1px solid var(--border);
  color: var(--muted);
  overflow-wrap: anywhere;
}

h3 {
  margin: 0 0 4px;
  font-size: 1rem;
}

ul {
  margin: 0;
  padding-left: 1.25em;
}
</style>
