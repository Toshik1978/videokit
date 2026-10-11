import '@/styles/base.css';

import { createApp } from 'vue';

import App from '@/App.vue';
import { createI18nInstance } from '@/i18n';
import { createAppRouter } from '@/router';

createApp(App).use(createAppRouter()).use(createI18nInstance()).mount('#app');
