import type { RouteRecordRaw } from 'vue-router';

export const id = 'youtube_prep';

export const routes: RouteRecordRaw[] = [
  {
    path: '/tools/youtube-prep',
    name: 'youtube_prep',
    component: () => import('./YouTubePrepPage.vue'),
  },
  {
    path: '/tools/youtube-prep/jobs/:id',
    name: 'youtube_prep_job',
    component: () => import('./JobPage.vue'),
  },
];
