import { createI18n } from 'vue-i18n';

import en from './en.json';

export function createI18nInstance() {
  return createI18n({
    legacy: false,
    locale: 'en',
    fallbackLocale: 'en',
    messages: { en },
  });
}
