import { useEffect, useMemo, useState, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthContext.tsx';
import Panel from '../components/ui/Panel.tsx';

type CurrentUser = { id: string; company_id?: string; email: string; full_name: string; role: string };
type AgencyRead = { id: string; agency_name?: string | null; gtfs_agency_id?: string | null };

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

export default function UserPage() {
  const { t } = useTranslation();
  const { token, logout, userId, setUserId } = useAuth();
  const baseUrl = useMemo(() => getEffectiveBaseUrl(), []);
  const [me, setMe] = useState<CurrentUser | null>(null);
  const [agencyName, setAgencyName] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);
  
  // Profile editing state
  const [editingProfile, setEditingProfile] = useState(false);
  const [profileForm, setProfileForm] = useState({ full_name: '', email: '' });
  const [profileError, setProfileError] = useState('');
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileSuccess, setProfileSuccess] = useState('');
  
  // Password editing state
  const [editingPassword, setEditingPassword] = useState(false);
  const [passwordForm, setPasswordForm] = useState({ current_password: '', new_password: '', confirm_password: '' });
  const [passwordError, setPasswordError] = useState('');
  const [passwordLoading, setPasswordLoading] = useState(false);
  const [passwordSuccess, setPasswordSuccess] = useState('');
  
  // Email validation state
  const [emailError, setEmailError] = useState('');
  const [emailChecking, setEmailChecking] = useState(false);
  const emailTimeoutRef = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!token) { setMe(null); setAgencyName(''); return; }
      try {
        setLoading(true);
        const meRes = await fetch(joinUrl(baseUrl, '/auth/me'), { headers: { Authorization: `Bearer ${token}` } });
        if (meRes.ok) {
          const user = (await meRes.json()) as CurrentUser;
          if (cancelled) return;
          setMe(user);
          if (user?.company_id) {
            try {
              const agRes = await fetch(joinUrl(baseUrl, `/api/v1/agency/agencies/${user.company_id}`), { headers: { Authorization: `Bearer ${token}` } });
              if (agRes.ok) {
                const agency = (await agRes.json()) as AgencyRead;
                if (!cancelled) setAgencyName(agency?.agency_name || '');
              }
            } catch {}
          }
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => { cancelled = true; };
  }, [token, baseUrl]);

  // Email validation function
  async function checkEmailAvailability(email: string) {
    if (!email || !email.includes('@') || email === me?.email) {
      setEmailError('');
      return;
    }
    
    try {
      setEmailChecking(true);
      setEmailError('');
      const url = joinUrl(baseUrl, `/auth/check-email/${encodeURIComponent(email)}`);
      const res = await fetch(url);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = await res.json();
      
      if (!data.available) {
        setEmailError(t('auth.emailAlreadyExists') as string);
      } else {
        setEmailError('');
      }
    } catch (e: any) {
      console.error('Email check failed:', e);
      setEmailError('');
    } finally {
      setEmailChecking(false);
    }
  }

  // Profile update function
  async function handleProfileUpdate() {
    setProfileError('');
    setProfileSuccess('');
    
    // Validation
    if (!profileForm.full_name || !profileForm.email) {
      setProfileError(t('auth.provideCredentials') as string);
      return;
    }
    
    if (emailError) {
      setProfileError(t('auth.emailAlreadyExists') as string);
      return;
    }
    
    try {
      setProfileLoading(true);
      const url = joinUrl(baseUrl, '/auth/me');
      const res = await fetch(url, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(profileForm)
      });
      
      if (!res.ok) {
        const errorData = await res.json();
        throw new Error(errorData.detail || `${res.status} ${res.statusText}`);
      }
      
      const updatedUser = await res.json();
      setMe(updatedUser);
      setProfileSuccess(t('auth.profileUpdated') as string);
      setEditingProfile(false);
      
      // Dispatch custom event to notify other components
      window.dispatchEvent(new CustomEvent('userProfileUpdated'));
    } catch (e: any) {
      setProfileError(e?.message || String(e));
    } finally {
      setProfileLoading(false);
    }
  }

  // Password update function
  async function handlePasswordUpdate() {
    setPasswordError('');
    setPasswordSuccess('');
    
    // Validation
    if (!passwordForm.current_password || !passwordForm.new_password || !passwordForm.confirm_password) {
      setPasswordError(t('auth.provideCredentials') as string);
      return;
    }
    
    if (passwordForm.new_password !== passwordForm.confirm_password) {
      setPasswordError(t('auth.passwordsDoNotMatch') as string);
      return;
    }
    
    try {
      setPasswordLoading(true);
      const url = joinUrl(baseUrl, '/auth/me/password');
      const res = await fetch(url, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          current_password: passwordForm.current_password,
          new_password: passwordForm.new_password
        })
      });
      
      if (!res.ok) {
        const errorData = await res.json();
        throw new Error(errorData.detail || `${res.status} ${res.statusText}`);
      }
      
      setPasswordSuccess(t('auth.passwordUpdated') as string);
      setPasswordForm({ current_password: '', new_password: '', confirm_password: '' });
      setEditingPassword(false);
      
      // Dispatch custom event to notify other components
      window.dispatchEvent(new CustomEvent('userProfileUpdated'));
    } catch (e: any) {
      setPasswordError(e?.message || String(e));
    } finally {
      setPasswordLoading(false);
    }
  }

  // Initialize profile form when user data loads
  useEffect(() => {
    if (me) {
      setProfileForm({ full_name: me.full_name, email: me.email });
    }
  }, [me]);

  return (
    <div className="space-y-4">
      <Panel>
        <h2 className="text-lg font-medium mb-2">{t('auth.userInfoTitle')}</h2>
        {loading && <div className="text-sm text-gray-600">{t('common.loading')}</div>}
        {!loading && (
          <div className="text-sm">
            {!editingProfile ? (
              <div>
                <div className="mb-1"><span className="font-semibold">{t('auth.userName')}:</span> {me?.full_name || '—'}</div>
                <div className="mb-1"><span className="font-semibold">{t('auth.userEmail')}:</span> {me?.email || '—'}</div>
                <div className="mb-1"><span className="font-semibold">{t('auth.agencyName')}:</span> {agencyName || '—'}</div>
                <div className="mb-3"><span className="font-semibold">{t('auth.userRole')}:</span> {me?.role || '—'}</div>
                <div className="flex gap-2">
                  <button 
                    onClick={() => setEditingProfile(true)} 
                    className="px-3 py-2 rounded-lg border text-sm hover:bg-gray-50"
                  >
                    {t('auth.editProfile')}
                  </button>
                  <button 
                    onClick={() => setEditingPassword(true)} 
                    className="px-3 py-2 rounded-lg border text-sm hover:bg-gray-50"
                  >
                    {t('auth.changePassword')}
                  </button>
                  <button onClick={logout} className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90" style={{backgroundColor: '#002AA7'}}>{t('auth.logout')}</button>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <h3 className="font-medium">{t('auth.editProfile')}</h3>
                {profileError && <div className="text-red-600 text-xs">{profileError}</div>}
                {profileSuccess && <div className="text-green-600 text-xs">{profileSuccess}</div>}
                <div>
                  <label className="block text-xs font-medium mb-1">{t('auth.userName')}</label>
                  <input 
                    className="w-full px-3 py-2 border rounded-lg text-sm" 
                    value={profileForm.full_name}
                    onChange={(e) => setProfileForm({...profileForm, full_name: e.target.value})}
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium mb-1">{t('auth.userEmail')}</label>
                  <input 
                    className={`w-full px-3 py-2 border rounded-lg text-sm ${emailError ? 'border-red-500' : ''}`}
                    value={profileForm.email}
                    onChange={(e) => {
                      setProfileForm({...profileForm, email: e.target.value});
                      // Clear previous timeout
                      if (emailTimeoutRef.current) {
                        clearTimeout(emailTimeoutRef.current);
                      }
                      // Set new timeout for debounced validation
                      emailTimeoutRef.current = setTimeout(() => {
                        checkEmailAvailability(e.target.value);
                      }, 500);
                    }}
                  />
                  {emailError && <div className="text-red-600 text-xs mt-1">{emailError}</div>}
                  {emailChecking && <div className="text-gray-500 text-xs mt-1">{t('common.loading')}</div>}
                </div>
                <div className="flex gap-2">
                  <button 
                    onClick={handleProfileUpdate}
                    disabled={profileLoading || !!emailError}
                    className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90 disabled:opacity-50" 
                    style={{backgroundColor: '#002AA7'}}
                  >
                    {profileLoading ? t('common.saving') : t('common.save')}
                  </button>
                  <button 
                    onClick={() => {
                      setEditingProfile(false);
                      setProfileError('');
                      setProfileSuccess('');
                      setEmailError('');
                      if (me) {
                        setProfileForm({ full_name: me.full_name, email: me.email });
                      }
                    }}
                    className="px-3 py-2 rounded-lg border text-sm hover:bg-gray-50"
                  >
                    {t('common.cancel')}
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </Panel>

      <Panel>
        <h2 className="text-lg font-medium mb-2">{t('shift.selectAgencyPlaceholder')}</h2>
        <div className="text-sm">
          <p className="text-gray-600 mb-2">{t('depots.selectAgencyBackend')}</p>
          <AgencySelector token={token} selectedId={userId} onSelect={setUserId} />
        </div>
      </Panel>

      {/* Password Change Modal */}
      {editingPassword && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <Panel className="max-w-md w-[95%]">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-base font-medium">{t('auth.changePassword')}</h3>
              <button 
                onClick={() => {
                  setEditingPassword(false);
                  setPasswordError('');
                  setPasswordSuccess('');
                  setPasswordForm({ current_password: '', new_password: '', confirm_password: '' });
                }} 
                className="text-sm text-neutral-600 hover:text-neutral-800"
              >
                {t('common.close')}
              </button>
            </div>
            {passwordError && <div className="mb-2 text-sm text-red-600">{passwordError}</div>}
            {passwordSuccess && <div className="mb-2 text-sm text-green-600">{passwordSuccess}</div>}
            <div className="space-y-3">
              <div>
                <label className="block text-sm font-medium mb-1">{t('auth.currentPassword')}</label>
                <input 
                  className="w-full px-3 py-2 border rounded-lg" 
                  type="password"
                  value={passwordForm.current_password}
                  onChange={(e) => setPasswordForm({...passwordForm, current_password: e.target.value})}
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">{t('auth.newPassword')}</label>
                <input 
                  className="w-full px-3 py-2 border rounded-lg" 
                  type="password"
                  value={passwordForm.new_password}
                  onChange={(e) => setPasswordForm({...passwordForm, new_password: e.target.value})}
                />
                <div className="mt-1 text-xs text-gray-600">
                  {t('auth.passwordRequirements')}
                </div>
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">{t('auth.confirmNewPassword')}</label>
                <input 
                  className="w-full px-3 py-2 border rounded-lg" 
                  type="password"
                  value={passwordForm.confirm_password}
                  onChange={(e) => setPasswordForm({...passwordForm, confirm_password: e.target.value})}
                />
              </div>
              <div className="flex gap-2 pt-2">
                <button 
                  onClick={handlePasswordUpdate}
                  disabled={passwordLoading}
                  className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90 disabled:opacity-50" 
                  style={{backgroundColor: '#002AA7'}}
                >
                  {passwordLoading ? t('common.saving') : t('auth.changePassword')}
                </button>
                <button 
                  onClick={() => {
                    setEditingPassword(false);
                    setPasswordError('');
                    setPasswordSuccess('');
                    setPasswordForm({ current_password: '', new_password: '', confirm_password: '' });
                  }}
                  className="px-3 py-2 rounded-lg border text-sm hover:bg-gray-50"
                >
                  {t('common.cancel')}
                </button>
              </div>
            </div>
          </Panel>
        </div>
      )}
    </div>
  );
}

function AgencySelector({ token, selectedId, onSelect }: { token: string; selectedId: string; onSelect: (id: string) => void }) {
  const { t } = useTranslation();
  const baseUrl = useMemo(() => getEffectiveBaseUrl(), []);
  const [agencies, setAgencies] = useState<AgencyRead[]>([]);
  const [query, setQuery] = useState<string>('');
  const [open, setOpen] = useState<boolean>(false);
  const [highlight, setHighlight] = useState<number>(-1);

  useEffect(() => {
    (async () => {
      if (!token || !baseUrl) return;
      try {
        const res = await fetch(joinUrl(baseUrl, '/api/v1/agency/agencies/?limit=1000'), { headers: { Authorization: `Bearer ${token}` } });
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        const data = (await res.json()) as AgencyRead[];
        setAgencies(Array.isArray(data) ? data : []);
      } catch {}
    })();
  }, [token, baseUrl]);

  useEffect(() => {
    if (!selectedId) return;
    const sel = agencies.find((a) => a.id === selectedId);
    if (sel) setQuery((sel.agency_name || sel.gtfs_agency_id || '') as string);
  }, [selectedId, agencies]);

  function label(a?: AgencyRead) { return (a?.agency_name || a?.gtfs_agency_id || '') as string; }

  return (
    <div className="relative max-w-md">
      <input
        className="w-full px-3 py-2 border rounded-lg"
        placeholder={t('shift.selectAgencyPlaceholder') as string}
        value={query}
        onFocus={() => token && setOpen(true)}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); setHighlight(-1); if (selectedId) onSelect(''); }}
        onKeyDown={(e) => {
          if (!open && (e.key === 'ArrowDown' || e.key === 'Enter')) { setOpen(true); return; }
          if (!open) return;
          if (e.key === 'ArrowDown') { e.preventDefault(); setHighlight((h) => Math.min((agencies.length - 1), h + 1)); }
          else if (e.key === 'ArrowUp') { e.preventDefault(); setHighlight((h) => Math.max(-1, h - 1)); }
          else if (e.key === 'Enter') {
            e.preventDefault();
            const pick = highlight >= 0 ? agencies[highlight] : agencies[0];
            if (pick) { onSelect(pick.id); setQuery(label(pick)); setOpen(false); setHighlight(-1); }
          } else if (e.key === 'Escape') { setOpen(false); setHighlight(-1); }
        }}
        onBlur={() => { setTimeout(() => setOpen(false), 100); }}
      />
      {open && token && agencies.length > 0 && (
        <ul className="absolute z-10 mt-1 w-full max-h-48 overflow-auto border rounded-lg bg-white shadow">
          {agencies
            .filter((a) => label(a).toLowerCase().includes(query.trim().toLowerCase()))
            .sort((a, b) => label(a).localeCompare(label(b), undefined, { sensitivity: 'base' }))
            .map((a, idx) => (
            <li
              key={a.id}
              className={"px-3 py-2 cursor-pointer text-sm " + (idx === highlight ? 'text-white' : 'hover:bg-gray-100')}
              style={idx === highlight ? {backgroundColor: '#002AA7'} : {}}
              onMouseEnter={() => setHighlight(idx)}
              onMouseDown={(e) => { e.preventDefault(); onSelect(a.id); setQuery(label(a)); setOpen(false); setHighlight(-1); }}
            >
              {label(a)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}


