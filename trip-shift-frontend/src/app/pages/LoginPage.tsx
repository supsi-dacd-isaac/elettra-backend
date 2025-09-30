import { useEffect, useState, useRef } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { useTranslation } from 'react-i18next';
import Panel from '../components/ui/Panel.tsx';

const COMMON_PASSWORDS = new Set([
  'password',
  'password1',
  'password123',
  '123456',
  '123456789',
  '12345678',
  'qwerty',
  'abc123',
  'letmein',
  '111111',
  '123123',
  '000000',
  'iloveyou',
  'admin',
  'welcome',
  'dragon',
]);

export default function LoginPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { setToken } = useAuth();
  const [searchParams] = useSearchParams();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [showRegister, setShowRegister] = useState(false);

  const [regFullName, setRegFullName] = useState('');
  const [regEmail, setRegEmail] = useState('');
  const [regPassword, setRegPassword] = useState('');
  const [regPasswordConfirm, setRegPasswordConfirm] = useState('');
  const [regPasswordError, setRegPasswordError] = useState('');
  const [regAgencyId, setRegAgencyId] = useState('');
  const [regLoading, setRegLoading] = useState(false);
  const [regError, setRegError] = useState('');
  const [regEmailError, setRegEmailError] = useState('');
  const [emailChecking, setEmailChecking] = useState(false);
  const emailTimeoutRef = useRef<number | null>(null);
  const [agencies, setAgencies] = useState<any[]>([]);
  const [agenciesLoading, setAgenciesLoading] = useState(false);
  const [agenciesError, setAgenciesError] = useState('');
  const [agencySearchTerm, setAgencySearchTerm] = useState('');
  const [showAgencyDropdown, setShowAgencyDropdown] = useState(false);
  const [agencyHighlightIndex, setAgencyHighlightIndex] = useState<number>(-1);
  const agencyContainerRef = useRef<HTMLDivElement | null>(null);

  function joinUrl(base: string, path: string): string {
    const cleanBase = (base || '').replace(/\/+$/, '');
    const cleanPath = path.startsWith('/') ? path : `/${path}`;
    return cleanBase ? `${cleanBase}${cleanPath}` : cleanPath;
  }

  function getEffectiveBaseUrl(): string {
    const VITE = (typeof import.meta !== 'undefined' ? (import.meta as any).env : {}) || {};
    const envBase = VITE.VITE_API_BASE_URL || '';
    if (envBase) return envBase as string;
    if (typeof window !== 'undefined') {
      const host = window.location.hostname;
      if (host === 'localhost' || host === '127.0.0.1') return 'http://localhost:8002';
      if (/^10\./.test(host)) return `http://${host}:8002`;
      if (host === 'isaac-elettra.dacd.supsi.ch') return 'http://isaac-elettra.dacd.supsi.ch:8002';
    }
    return 'http://localhost:8002';
  }

  async function loginWithCredentials() {
    setError('');
    setNotice('');
    if (!email || !password) {
      setError(t('auth.provideCredentials') as string);
      return;
    }
    try {
      setLoading(true);
      const base = getEffectiveBaseUrl();
      const url = joinUrl(base, '/auth/login');
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = await res.json();
      const tok = data?.access_token;
      if (!tok) throw new Error(t('auth.errors.noToken') as string);
      setToken(tok);
      navigate('/planner', { replace: true });
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    // Open register modal if ?register=1
    const wantRegister = searchParams.get('register') === '1';
    if (wantRegister) setShowRegister(true);
  }, [searchParams]);

  useEffect(() => {
    let cancelled = false;
    async function loadAgencies() {
      if (!showRegister) return;
      try {
        setAgenciesError('');
        setAgenciesLoading(true);
        const base = getEffectiveBaseUrl();
        const url = joinUrl(base, '/api/v1/agency/agencies/?limit=1000');
        const res = await fetch(url);
        if (!res.ok) throw new Error(`${res.status}`);
        const data = await res.json();
        if (!cancelled && Array.isArray(data)) setAgencies(data);
      } catch (e) {
        if (!cancelled) {
          setAgencies([]);
          setAgenciesError('load');
        }
      } finally {
        if (!cancelled) setAgenciesLoading(false);
      }
    }
    void loadAgencies();
    return () => { cancelled = true; };
  }, [showRegister]);

  // Load agencies with search term
  useEffect(() => {
    let cancelled = false;
    let timeoutId: number;
    
    async function searchAgencies() {
      if (!showRegister) return;
      
      try {
        setAgenciesLoading(true);
        setAgenciesError('');
        const base = getEffectiveBaseUrl();
        const searchParam = agencySearchTerm ? `&search=${encodeURIComponent(agencySearchTerm)}` : '';
        const url = joinUrl(base, `/api/v1/agency/agencies/?limit=1000${searchParam}`);
        const res = await fetch(url);
        if (!res.ok) throw new Error(`${res.status}`);
        const data = await res.json();
        if (!cancelled && Array.isArray(data)) setAgencies(data);
      } catch (e) {
        if (!cancelled) {
          setAgencies([]);
          setAgenciesError('load');
        }
      } finally {
        if (!cancelled) setAgenciesLoading(false);
      }
    }

    // Debounce search requests
    timeoutId = window.setTimeout(() => {
      void searchAgencies();
    }, 300);

    return () => {
      cancelled = true;
      clearTimeout(timeoutId);
    };
  }, [agencySearchTerm, showRegister]);

  // Reset agency highlight when list changes or opens
  useEffect(() => {
    setAgencyHighlightIndex(agencies.length > 0 && showAgencyDropdown ? 0 : -1);
  }, [agencies, showAgencyDropdown]);

  // Close agency dropdown on outside click or Escape
  useEffect(() => {
    function handleDocMouseDown(e: MouseEvent) {
      if (agencyContainerRef.current && !agencyContainerRef.current.contains(e.target as Node)) {
        setShowAgencyDropdown(false);
      }
    }
    function handleDocKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        setShowAgencyDropdown(false);
      }
    }
    document.addEventListener('mousedown', handleDocMouseDown);
    document.addEventListener('keydown', handleDocKeyDown);
    return () => {
      document.removeEventListener('mousedown', handleDocMouseDown);
      document.removeEventListener('keydown', handleDocKeyDown);
    };
  }, []);

  useEffect(() => {
    if (!regPassword) {
      setRegPasswordError('');
      return;
    }
    const validationError = validatePasswordStrength(regPassword);
    setRegPasswordError(validationError ?? '');
  }, [regPassword]);


  function hasSequentialCharacters(password: string, length: number = 3): boolean {
    const lowered = password.toLowerCase();
    const sequences = ['abcdefghijklmnopqrstuvwxyz', 'zyxwvutsrqponmlkjihgfedcba', '0123456789', '9876543210'];
    for (const sequence of sequences) {
      for (let i = 0; i <= sequence.length - length; i += 1) {
        const fragment = sequence.slice(i, i + length);
        if (lowered.includes(fragment)) {
          return true;
        }
      }
    }
    return false;
  }

  function validatePasswordStrength(password: string): string | null {
    const errors: string[] = [];
    if (password.length < 12) errors.push(t('auth.passwordRequirements.length', 'Password must be at least 12 characters long') as string);
    if (!/[a-z]/.test(password)) errors.push(t('auth.passwordRequirements.lowercase', 'Password must contain at least 1 lowercase letter') as string);
    if (!/[A-Z]/.test(password)) errors.push(t('auth.passwordRequirements.uppercase', 'Password must contain at least 1 uppercase letter') as string);
    if (!/[0-9]/.test(password)) errors.push(t('auth.passwordRequirements.digits', 'Password must contain at least 1 digit') as string);
    if (/(.)\1\1/.test(password)) errors.push(t('auth.passwordRequirements.repeated', 'Password cannot contain the same character three or more times in a row') as string);
    if (hasSequentialCharacters(password)) errors.push(t('auth.passwordRequirements.sequential', "Password cannot contain sequential characters like 'abc' or '123'") as string);
    if (COMMON_PASSWORDS.has(password.toLowerCase())) errors.push(t('auth.passwordRequirements.common', 'Password is too common') as string);

    if (errors.length > 0) {
      return errors.join(' ');
    }
    return null;
  }

  async function checkEmailAvailability(email: string) {
    if (!email || !email.includes('@')) {
      setRegEmailError('');
      return;
    }

    try {
      setEmailChecking(true);
      setRegEmailError('');
      const base = getEffectiveBaseUrl();
      const url = joinUrl(base, `/auth/check-email/${encodeURIComponent(email)}`);
      const res = await fetch(url);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = await res.json();

      if (!data.available) {
        setRegEmailError(t('auth.emailAlreadyExists') as string);
      } else {
        setRegEmailError('');
      }
    } catch (e: any) {
      console.error('Email check failed:', e);
      setRegEmailError('');
    } finally {
      setEmailChecking(false);
    }
  }

  function selectAgency(agency: any) {
    setRegAgencyId(agency.id);
    setAgencySearchTerm(agency.agency_name || agency.gtfs_agency_id || agency.id);
    setShowAgencyDropdown(false);
  }

  async function handleRegister() {
    setRegError('');
    setNotice('');
    if (!regFullName || !regEmail || !regPassword || !regPasswordConfirm || !regAgencyId) {
      setRegError(t('auth.provideCredentials') as string);
      return;
    }

    if (regPassword !== regPasswordConfirm) {
      setRegError(t('auth.passwordsDoNotMatch') as string);
      return;
    }

    const strengthError = validatePasswordStrength(regPassword);
    if (strengthError) {
      setRegError(strengthError);
      return;
    }

    if (regEmailError) {
      setRegError(t('auth.emailAlreadyExists') as string);
      return;
    }
    try {
      setRegLoading(true);
      const base = getEffectiveBaseUrl();
      const url = joinUrl(base, '/auth/register');
      const body = {
        company_id: regAgencyId,
        email: regEmail,
        full_name: regFullName,
        password: regPassword,
      };
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      setShowRegister(false);
      setEmail(regEmail);
      setNotice(t('auth.registrationSuccess') as string);
      setRegFullName('');
      setRegEmail('');
      setRegPassword('');
      setRegPasswordConfirm('');
      setRegAgencyId('');
      setRegEmailError('');
    } catch (e: any) {
      setRegError(e?.message || String(e));
    } finally {
      setRegLoading(false);
    }
  }

  return (
    <div className="max-w-md mx-auto">
      <Panel>
        <h2 className="text-lg font-medium mb-3">{t('auth.title')}</h2>
        {notice && <div className="mb-2 text-sm text-green-700">{notice}</div>}
        {error && <div className="mb-2 text-sm text-red-600">{error}</div>}
        <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); loginWithCredentials(); }}>
          <div className="grid grid-cols-2 gap-2">
            <input 
              className="px-3 py-2 border rounded-lg" 
              type="email"
              name="email"
              id="login-email"
              autoComplete="email"
              placeholder={t('auth.emailPlaceholder') as string} 
              value={email} 
              onChange={(e) => setEmail(e.target.value)} 
            />
            <input 
              className="px-3 py-2 border rounded-lg" 
              type="password" 
              name="password"
              id="login-password"
              autoComplete="current-password"
              placeholder={t('auth.passwordPlaceholder') as string} 
              value={password} 
              onChange={(e) => setPassword(e.target.value)} 
            />
          </div>
          <div className="flex items-center gap-2">
            <button type="submit" className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90" style={{ backgroundColor: '#002AA7' }} disabled={loading}>
              {t(loading ? 'auth.loggingIn' : 'auth.login')}
            </button>
            <button type="button" onClick={() => { setShowRegister(true); setRegError(''); }} className="px-3 py-2 rounded-lg border text-sm hover:bg-neutral-50" disabled={loading}>
              {t('auth.register')}
            </button>
          </div>
        </form>
      </Panel>

      {showRegister && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <Panel className="max-w-md w-[95%]">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-base font-medium">{t('auth.registerTitle')}</h3>
              <button onClick={() => setShowRegister(false)} className="text-sm text-neutral-600 hover:text-neutral-800">{t('common.close')}</button>
            </div>
            {regError && <div className="mb-2 text-sm text-red-600">{regError}</div>}
            <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); handleRegister(); }} data-form-type="registration">
              <label htmlFor="reg-full-name" className="sr-only">{t('auth.fullNamePlaceholder')}</label>
              <input 
                className="w-full px-3 py-2 border rounded-lg" 
                type="text"
                name="fullName"
                id="reg-full-name"
                autoComplete="name"
                required
                placeholder={t('auth.fullNamePlaceholder') as string} 
                value={regFullName} 
                onChange={(e) => setRegFullName(e.target.value)} 
              />
              <div>
                <label htmlFor="reg-email" className="sr-only">{t('auth.emailPlaceholder')}</label>
                <input
                  className={`w-full px-3 py-2 border rounded-lg ${regEmailError ? 'border-red-500' : ''}`}
                  type="email"
                  name="email"
                  id="reg-email"
                  autoComplete="username"
                  required
                  placeholder={t('auth.emailPlaceholder') as string}
                  value={regEmail}
                  onChange={(e) => {
                    setRegEmail(e.target.value);
                    if (emailTimeoutRef.current) {
                      clearTimeout(emailTimeoutRef.current);
                    }
                    emailTimeoutRef.current = window.setTimeout(() => {
                      checkEmailAvailability(e.target.value);
                    }, 500);
                  }}
                  disabled={regLoading}
                />
                {regEmailError && <div className="mt-1 text-xs text-red-600">{regEmailError}</div>}
                {emailChecking && <div className="mt-1 text-xs text-gray-500">{t('common.loading')}</div>}
              </div>
              <div className="relative" role="combobox" aria-haspopup="listbox" aria-expanded={showAgencyDropdown} aria-owns="agency-listbox" aria-controls="agency-listbox" ref={agencyContainerRef as any}>
                <label className="block text-sm mb-1">{t('auth.selectAgencyLabel')}</label>
                <div className="relative">
                  <input
                    type="text"
                    className="w-full px-3 py-2 border rounded-lg pr-8"
                    placeholder={agenciesLoading ? (t('common.loading') as string) : (t('auth.selectAgencyPlaceholder') as string)}
                    value={agencySearchTerm}
                  onChange={(e) => {
                      setAgencySearchTerm(e.target.value);
                      setShowAgencyDropdown(true); // open when typing for live filtering
                    }}
                    onClick={() => setShowAgencyDropdown(true)}
                    onFocus={() => setShowAgencyDropdown(true)}
                    onKeyDown={(e) => {
                      if (!showAgencyDropdown && (e.key.length === 1 || e.key === 'ArrowDown')) {
                        setShowAgencyDropdown(true);
                      }
                      if (!showAgencyDropdown) return;
                      if (e.key === 'ArrowDown') {
                        e.preventDefault();
                        setAgencyHighlightIndex((prev) => Math.min((prev < 0 ? 0 : prev) + 1, agencies.length - 1));
                      } else if (e.key === 'ArrowUp') {
                        e.preventDefault();
                        setAgencyHighlightIndex((prev) => Math.max((prev < 0 ? 0 : prev) - 1, 0));
                      } else if (e.key === 'Enter') {
                        e.preventDefault();
                        const idx = agencyHighlightIndex < 0 ? 0 : agencyHighlightIndex;
                        const ag = agencies[idx];
                        if (ag) selectAgency(ag);
                      } else if (e.key === 'Escape') {
                        setShowAgencyDropdown(false);
                      }
                    }}
                    aria-autocomplete="list"
                    aria-controls="agency-listbox"
                    aria-expanded={showAgencyDropdown}
                    role="textbox"
                    data-lpignore="true"
                    autoComplete="off"
                  />
                  <button
                    type="button"
                    className="absolute right-2 top-1/2 transform -translate-y-1/2 text-gray-400 hover:text-gray-600"
                    onClick={() => setShowAgencyDropdown(!showAgencyDropdown)}
                    disabled={agenciesLoading || agencies.length === 0}
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                  </button>
                </div>
                
                {showAgencyDropdown && agencies.length > 0 && (
                  <ul id="agency-listbox" role="listbox" className="absolute z-10 w-full mt-1 bg-white border border-gray-300 rounded-lg shadow-lg max-h-60 overflow-y-auto">
                    {agencies.map((agency, idx) => (
                      <li
                        id={`agency-option-${agency.id}`}
                        role="option"
                        aria-selected={agencyHighlightIndex === idx}
                        key={agency.id}
                        className={`px-3 py-2 cursor-pointer ${agencyHighlightIndex === idx ? 'bg-gray-100' : 'hover:bg-gray-100'}`}
                        onMouseEnter={() => setAgencyHighlightIndex(idx)}
                        onMouseDown={(e) => { e.preventDefault(); selectAgency(agency); }}
                      >
                        {agency.agency_name || agency.gtfs_agency_id || agency.id}
                      </li>
                    ))}
                  </ul>
                )}
                
                {showAgencyDropdown && agencies.length === 0 && agencySearchTerm && !agenciesLoading && (
                  <div className="absolute z-10 w-full mt-1 bg-white border border-gray-300 rounded-lg shadow-lg">
                    <div className="px-3 py-2 text-gray-500 text-sm">
                      No agencies found matching "{agencySearchTerm}"
                    </div>
                  </div>
                )}
                
                {agenciesError && <div className="mt-1 text-xs text-neutral-500">{t('auth.couldNotLoadAgencies')}</div>}
              </div>
              <div>
                <input
                  className={`w-full px-3 py-2 border rounded-lg ${regPasswordError ? 'border-red-500' : ''}`}
                  type="password"
                  name="new-password"
                  id="reg-password"
                  autoComplete="new-password"
                  data-lpignore="true"
                  placeholder={t('auth.passwordPlaceholder') as string}
                  value={regPassword}
                  onChange={(e) => setRegPassword(e.target.value)}
                  disabled={regLoading}
                />
                <div className="mt-1 text-xs text-neutral-600">
                  {t('auth.passwordGuidelines')}
                </div>
                {regPasswordError && <div className="mt-1 text-xs text-red-600">{regPasswordError}</div>}
              </div>
              <div>
                <input
                  className={`w-full px-3 py-2 border rounded-lg ${regPassword && regPassword !== regPasswordConfirm ? 'border-red-500' : ''}`}
                  type="password"
                  name="password-confirm"
                  id="reg-confirm-password"
                  autoComplete="new-password"
                  data-lpignore="true"
                  placeholder={t('auth.passwordConfirmPlaceholder') as string}
                  value={regPasswordConfirm}
                  onChange={(e) => setRegPasswordConfirm(e.target.value)}
                  disabled={regLoading}
                />
              </div>
              <div className="flex items-center gap-2 pt-1">
                <button type="submit" className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90" style={{ backgroundColor: '#002AA7' }} disabled={regLoading || !regAgencyId}>
                  {t(regLoading ? 'auth.registering' : 'auth.register')}
                </button>
                <button type="button" onClick={() => setShowRegister(false)} className="px-3 py-2 rounded-lg border text-sm" disabled={regLoading}>{t('common.cancel')}</button>
              </div>
            </form>
          </Panel>
        </div>
      )}
    </div>
  );
}


