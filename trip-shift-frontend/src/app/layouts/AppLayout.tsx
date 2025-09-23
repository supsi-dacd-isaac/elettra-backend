import { Outlet, NavLink, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { SUPPORTED_LANGUAGES, setAppLanguage } from '../../i18n';
import { useAuth } from '../auth/AuthContext';
import { useEffect, useMemo, useState, useRef } from 'react';

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
  const { token, logout, refreshUser } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [me, setMe] = useState<any>(null);
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const baseUrl = useMemo(() => {
    const VITE = (typeof import.meta !== 'undefined' ? (import.meta as any).env : {}) || {};
    const envBase = (VITE as any).VITE_API_BASE_URL || '';
    if (envBase) return envBase as string;
    if (typeof window !== 'undefined') {
      const host = window.location.hostname;
      if (host === 'localhost' || host === '127.0.0.1') return 'http://localhost:8002';
      if (/^10\./.test(host)) return `http://${host}:8002`;
      if (host === 'isaac-elettra.dacd.supsi.ch') return 'http://isaac-elettra.dacd.supsi.ch:8002';
    }
    return 'http://localhost:8002';
  }, []);
  useEffect(() => {
    let cancelled = false;
    async function loadMe() {
      if (!token) { setMe(null); return; }
      try {
        const res = await fetch(`${baseUrl}/auth/me`, { headers: { Authorization: `Bearer ${token}` } });
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled) setMe(data);
      } catch {}
    }
    void loadMe();
    return () => { cancelled = true; };
  }, [token, baseUrl, refreshTrigger]);

  // Listen for user profile updates
  useEffect(() => {
    function handleUserUpdate() {
      refreshUser();
      setRefreshTrigger(prev => prev + 1);
    }

    window.addEventListener('userProfileUpdated', handleUserUpdate);
    return () => {
      window.removeEventListener('userProfileUpdated', handleUserUpdate);
    };
  }, [refreshUser]);

  // Click outside to close dropdown
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }

    if (open) {
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [open]);
  if (!token) {
    return (
      <div className="flex items-center gap-2">
        <button onClick={() => navigate('/login')} className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90" style={{backgroundColor: '#002AA7'}}>
          {t('auth.login')}
        </button>
      </div>
    );
  }
  return (
    <div className="relative" ref={dropdownRef}>
      <button aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((v) => !v)} className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90" style={{backgroundColor: '#002AA7'}}>
        {(me?.full_name || me?.email || 'User') as string}
      </button>
      {open && (
        <div role="menu" aria-label={t('nav.user')} className="absolute right-0 mt-2 w-64 bg-white border rounded-xl shadow-lg p-3 z-50">
          <div className="text-sm mb-2">
            <div className="font-medium">{me?.full_name || t('auth.userName')}</div>
            <div className="text-gray-600">{me?.email || t('auth.userEmail')}</div>
            {me?.role && <div className="text-gray-600">{t('auth.userRole')}: {me.role}</div>}
          </div>
          <div className="flex items-center justify-between gap-2">
            <button onClick={() => { setOpen(false); navigate('/user'); }} className="px-3 py-2 rounded bg-neutral-100 hover:bg-neutral-200 text-sm">{t('nav.user')}</button>
            <button onClick={() => { setOpen(false); logout(); }} className="px-3 py-2 rounded text-white text-sm hover:opacity-90" style={{backgroundColor: '#002AA7'}}>{t('auth.logout')}</button>
          </div>
        </div>
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


