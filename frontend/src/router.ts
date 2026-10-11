import { createRouter, createWebHistory, type RouterHistory } from 'vue-router';

import HomePage from '@/pages/HomePage.vue';
import NotFoundPage from '@/pages/NotFoundPage.vue';
import { type ToolModule, tools as discoveredTools } from '@/tools';

export function createAppRouter(
  history: RouterHistory = createWebHistory(),
  tools: ToolModule[] = discoveredTools,
) {
  return createRouter({
    history,
    routes: [
      { path: '/', name: 'home', component: HomePage },
      ...tools.flatMap((tool) => tool.routes),
      { path: '/:pathMatch(.*)*', name: 'not-found', component: NotFoundPage },
    ],
  });
}
