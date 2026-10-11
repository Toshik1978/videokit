import type { RouteRecordRaw } from 'vue-router';

export const id = 'youtube_prep';

export const labels = {
  name: 'tools.youtube_prep.name',
  description: 'tools.youtube_prep.description',
};

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
