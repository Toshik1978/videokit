import type { RouteRecordRaw } from 'vue-router';

export const id = 'video_generation';

export const labels = {
  name: 'tools.video_generation.name',
  description: 'tools.video_generation.description',
};

export const routes: RouteRecordRaw[] = [
  {
    path: '/tools/video-generation',
    name: 'video_generation',
    component: () => import('./VideoGenerationPage.vue'),
  },
  {
    path: '/tools/video-generation/jobs/:id',
    name: 'video_generation_job',
    component: () => import('./JobPage.vue'),
  },
];
