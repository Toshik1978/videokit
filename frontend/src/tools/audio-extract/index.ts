import type { RouteRecordRaw } from 'vue-router';

export const id = 'audio_extract';

export const routes: RouteRecordRaw[] = [
  {
    path: '/tools/audio-extract',
    name: 'audio_extract',
    component: () => import('./AudioExtractPage.vue'),
  },
  {
    path: '/tools/audio-extract/jobs/:id',
    name: 'audio_extract_job',
    component: () => import('./JobPage.vue'),
  },
];
