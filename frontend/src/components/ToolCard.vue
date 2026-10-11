<script setup lang="ts">
defineProps<{ name: string; description: string; to: string }>();
</script>

<template>
  <RouterLink class="tool-card" :to="to" data-test="tool-card">
    <span class="tool-card-name">{{ name }}</span>
    <span class="tool-card-description">{{ description }}</span>
  </RouterLink>
</template>

<style scoped>
.tool-card {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
  min-height: var(--touch);
  padding: var(--gutter);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: inherit;
  text-decoration: none;
  overflow-wrap: anywhere;
}

.tool-card-name {
  font-weight: 700;
  color: var(--accent);
}

.tool-card-description {
  color: var(--muted);
}
</style>
