<script setup lang="ts">
import { useI18n } from 'vue-i18n';

const { t } = useI18n();
</script>

<template>
  <section class="page">
    <h2>{{ t('home.heading') }}</h2>
    <p>{{ t('home.empty') }}</p>
  </section>
</template>
