import { Outlet, NavLink, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { SUPPORTED_LANGUAGES, setAppLanguage } from '../../i18n';
import { useAuth } from '../auth/AuthContext';

function LanguageSwitcher() {
  const { t, i18n } = useTranslation();
  const current = i18n.language;
  return (
    <label className="flex items-center gap-2 text-sm text-gray-600">
      <span className="font-medium text-gray-700">{t('header.languageLabel')}</span>
      <select
        className="px-3 py-2 border rounded-lg bg-white text-gray-900"
        value={current}
        onChange={(e) => setAppLanguage(e.target.value as any)}
      >
        {SUPPORTED_LANGUAGES.map((lng) => (
          <option key={lng} value={lng}>{t(`languageNames.${lng}`)}</option>
        ))}
      </select>
    </label>
  );
}

function AuthStatus() {
  const { t } = useTranslation();
  const { token, logout } = useAuth();
  const navigate = useNavigate();
  return (
    <div className="flex items-center gap-2">
      {token ? (
        <button onClick={logout} className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90" style={{backgroundColor: '#002AA7'}}>
          {t('auth.logout')}
        </button>
      ) : (
        <button onClick={() => navigate('/login')} className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90" style={{backgroundColor: '#002AA7'}}>
          {t('auth.login')}
        </button>
      )}
    </div>
  );
}

export default function AppLayout() {
  const { t } = useTranslation();
  return (
    <div className="min-h-screen w-full bg-gray-50 text-gray-900">
      <header className="sticky top-0 z-10 bg-white/80 backdrop-blur border-b">
        <div className="mx-auto max-w-7xl px-4 py-3 flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
          <div className="flex items-center gap-3">
            <img src="/elettra_icon.svg" alt="Elettra" className="w-12 h-12" />
            <div>
              <h1 className="text-2xl font-semibold">{t('header.title')}</h1>
              <p className="text-sm text-gray-600">{t('header.subtitle')}</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <LanguageSwitcher />
            <AuthStatus />
          </div>
        </div>
        <nav className="mx-auto max-w-7xl px-4 pb-2 flex gap-3 text-sm">
          {/* Planner removed */}
          <NavLink to="/shifts" className={(opts: { isActive: boolean }) => (opts.isActive ? 'font-semibold' : '')}>{t('nav.shifts', 'Shifts')}</NavLink>
          <NavLink to="/depots" className={(opts: { isActive: boolean }) => (opts.isActive ? 'font-semibold' : '')}>{t('nav.depots', 'Depots')}</NavLink>
          <NavLink to="/fleet/models" className={(opts: { isActive: boolean }) => (opts.isActive ? 'font-semibold' : '')}>{t('nav.busModels', 'Bus models')}</NavLink>
          <NavLink to="/fleet/buses" className={(opts: { isActive: boolean }) => (opts.isActive ? 'font-semibold' : '')}>{t('nav.buses', 'Buses')}</NavLink>
          <NavLink to="/user" className={(opts: { isActive: boolean }) => (opts.isActive ? 'font-semibold' : '')}>{t('nav.user', 'User')}</NavLink>
        </nav>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-4">
        <Outlet />
      </main>
    </div>
  );
}


