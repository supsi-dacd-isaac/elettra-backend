import { useTranslation } from 'react-i18next';

export default function Footer() {
  const { t } = useTranslation();

  return (
    <footer className="bg-white border-t border-gray-200 mt-auto">
      <div className="mx-auto max-w-7xl px-4 py-6">
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
          <div className="flex items-center gap-3">
            <img src="/elettra_icon.svg" alt="Elettra" className="w-8 h-8" />
            <div>
              <p className="text-sm text-gray-600">{t('footer.copyright')}</p>
              <p className="text-xs text-gray-500">{t('footer.institution')}</p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <a
              href="https://www.supsi.ch/informativa-privacy"
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-gray-600 hover:text-gray-900 transition-colors"
            >
              {t('footer.privacyPolicy')}
            </a>
          </div>
        </div>
      </div>
    </footer>
  );
}
