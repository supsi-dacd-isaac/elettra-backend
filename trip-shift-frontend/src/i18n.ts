import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import en from './locales/en/translation.json';
import it from './locales/it/translation.json';
import de from './locales/de/translation.json';
import fr from './locales/fr/translation.json';

export const SUPPORTED_LANGUAGES = ['en', 'it', 'de', 'fr'] as const;
export type SupportedLanguage = typeof SUPPORTED_LANGUAGES[number];

const STORAGE_KEY = 'tripShiftPlanner.language';

const resources = {
  en: { translation: en },
  it: { translation: it },
  de: { translation: de },
  fr: { translation: fr },
} satisfies Record<SupportedLanguage, { translation: Record<string, unknown> }>;

function detectInitialLanguage(): SupportedLanguage {
  if (typeof window === 'undefined') return 'en';
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored && SUPPORTED_LANGUAGES.includes(stored as SupportedLanguage)) {
      return stored as SupportedLanguage;
    }
  } catch {
    // ignore storage errors
  }

  const browserLang = window.navigator.languages?.[0] || window.navigator.language || '';
  const normalized = browserLang.split('-')[0].toLowerCase();
  if (SUPPORTED_LANGUAGES.includes(normalized as SupportedLanguage)) {
    return normalized as SupportedLanguage;
  }
  return 'en';
}

if (!i18n.isInitialized) {
  i18n.use(initReactI18next).init({
    resources,
    lng: 'en',
    fallbackLng: 'en',
    supportedLngs: SUPPORTED_LANGUAGES,
    interpolation: { escapeValue: false },
    returnNull: false,
  });
} else {
  // Hot-reload friendly: refresh resources if already initialised
  for (const lng of SUPPORTED_LANGUAGES) {
    i18n.addResourceBundle(lng, 'translation', resources[lng].translation, true, true);
  }
}

const initialLanguage = detectInitialLanguage();
if (i18n.isInitialized && initialLanguage !== i18n.language) {
  void i18n.changeLanguage(initialLanguage);
} else if (!i18n.isInitialized) {
  // i18n will emit languageChanged after init; ensure stored language is applied once ready
  i18n.on('initialized', () => {
    if (initialLanguage !== i18n.language) {
      void i18n.changeLanguage(initialLanguage);
    }
  });
}

const LISTENER_FLAG = '__tripShiftPlannerLanguageListener';
if (typeof window !== 'undefined') {
  const globalBag = window as unknown as Record<string, unknown>;
  if (!globalBag[LISTENER_FLAG]) {
    i18n.on('languageChanged', (lng: string) => {
      if (SUPPORTED_LANGUAGES.includes(lng as SupportedLanguage)) {
        try {
          window.localStorage.setItem(STORAGE_KEY, lng);
        } catch {
          // ignore storage errors
        }
      }
    });
    globalBag[LISTENER_FLAG] = true;
  }
}

export function setAppLanguage(lng: SupportedLanguage) {
  if (!SUPPORTED_LANGUAGES.includes(lng)) return;
  void i18n.changeLanguage(lng);
}

export function getCurrentLanguage(): SupportedLanguage {
  return (i18n.language as SupportedLanguage) || 'en';
}

export default i18n;
