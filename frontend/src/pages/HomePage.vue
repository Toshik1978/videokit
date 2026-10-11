<script setup lang="ts">
import { computed, inject, onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';

import { getJson } from '@/api/client';
import ToolCard from '@/components/ToolCard.vue';
import { tools as discoveredTools, toolsKey } from '@/tools';

interface BackendTool {
  id: string;
  name: string;
  description: string;
}

const { t } = useI18n();
const modules = inject(toolsKey, discoveredTools);

const state = ref<'loading' | 'ready' | 'error'>('loading');
const backendTools = ref<BackendTool[]>([]);

// One card per backend tool that has a frontend module (the module's `id` equals the tool id).
const cards = computed(() =>
  backendTools.value.flatMap((tool) => {
    const path = modules.find((m) => m.id === tool.id)?.routes[0]?.path;
    return path === undefined ? [] : [{ ...tool, path }];
  }),
);

onMounted(async () => {
  try {
    backendTools.value = await getJson<BackendTool[]>('/api/tools');
    state.value = 'ready';
  } catch {
    state.value = 'error';
  }
});
</script>

<template>
  <section class="page">
    <h2>{{ t('home.heading') }}</h2>
    <p v-if="state === 'loading'" role="status">{{ t('home.loading') }}</p>
    <p v-else-if="state === 'error'" role="alert">{{ t('errors.generic') }}</p>
    <p v-else-if="cards.length === 0">{{ t('home.empty') }}</p>
    <ul v-else class="tool-list">
      <li v-for="card in cards" :key="card.id">
        <ToolCard :name="card.name" :description="card.description" :to="card.path" />
      </li>
    </ul>
  </section>
</template>

<style scoped>
.tool-list {
  display: grid;
  gap: var(--gutter);
  margin: 0;
  padding: 0;
  list-style: none;
}
</style>
