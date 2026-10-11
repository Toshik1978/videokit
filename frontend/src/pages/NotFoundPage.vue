<script setup lang="ts">
import { useI18n } from 'vue-i18n';

const { t } = useI18n();
</script>

<template>
  <section class="page">
    <h2>{{ t('notFound.title') }}</h2>
    <p>{{ t('notFound.body') }}</p>
    <RouterLink class="button" to="/">{{ t('notFound.back') }}</RouterLink>
  </section>
</template>
