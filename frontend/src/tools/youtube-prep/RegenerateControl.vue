<script setup lang="ts">
import { ref } from 'vue';
import { useI18n } from 'vue-i18n';

import { ApiError, postJson } from '@/api/client';
import { useMessages } from '@/i18n/messages';

import type { RegenerateResponse } from './types';

const MAX_HINT = 500;

const props = defineProps<{ jobId: string; section: string }>();
const emit = defineEmits<{ regenerated: [response: RegenerateResponse] }>();

const { t } = useI18n();
const { errorMessage } = useMessages();
const hint = ref('');
const busy = ref(false);
const error = ref('');

function messageFor(err: unknown): string {
  return err instanceof ApiError && err.status === 404 ? t('job.expired') : errorMessage(err);
}

async function regenerate(): Promise<void> {
  if (busy.value) return;
  busy.value = true;
  error.value = '';
  try {
    const response = await postJson<RegenerateResponse>(
      `/api/tools/youtube_prep/jobs/${encodeURIComponent(props.jobId)}/regenerate`,
      hint.value ? { section: props.section, hint: hint.value } : { section: props.section },
    );
    emit('regenerated', response);
  } catch (err) {
    error.value = messageFor(err);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="regenerate">
    <label class="hint-field">
      <span class="hint-label">{{ t('regenerate.hintLabel') }}</span>
      <input
        v-model="hint"
        type="text"
        data-test="hint"
        :maxlength="MAX_HINT"
        :placeholder="t('regenerate.hintPlaceholder')"
      />
      <span class="count">{{
        t('regenerate.hintCount', { count: hint.length, max: MAX_HINT })
      }}</span>
    </label>
    <button type="button" data-test="regenerate" :disabled="busy" @click="regenerate">
      {{ busy ? t('regenerate.running') : t('regenerate.button') }}
    </button>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
  </div>
</template>

<style scoped>
.regenerate {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 8px;
  width: 100%;
}

.hint-field {
  flex: 1 1 200px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.hint-field input {
  min-height: var(--touch);
  width: 100%;
  max-width: 100%;
}

.hint-label,
.count {
  color: var(--muted);
}

button {
  min-width: var(--touch);
  min-height: var(--touch);
}

.error {
  flex-basis: 100%;
  margin: 0;
  color: #b00020;
}
</style>
