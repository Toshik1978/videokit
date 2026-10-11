<script setup lang="ts">
import { useI18n } from 'vue-i18n';

const { t } = useI18n();
</script>

<template>
  <header class="app-header">
    <RouterLink class="brand" to="/">{{ t('app.title') }}</RouterLink>
  </header>
  <main class="app-main">
    <RouterView />
  </main>
</template>
