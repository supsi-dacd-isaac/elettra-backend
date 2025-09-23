import { useEffect, useRef, useState } from 'react';
import { MapContainer, Marker, TileLayer, useMap } from 'react-leaflet';
import { useTranslation } from 'react-i18next';

function joinUrl(base: string, path: string): string {
  const cleanBase = (base || '').replace(/\/+$/, '');
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  return cleanBase ? `${cleanBase}${cleanPath}` : cleanPath;
}

function stripHtml(input: string): string {
  try {
    return (input || '').replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim();
  } catch {
    return input;
  }
}

type Depot = { 
  id: string; 
  user_id: string; 
  name: string; 
  address?: string | null; 
  features?: any; 
  stop_id?: string | null; 
  latitude?: number | null; 
  longitude?: number | null 
};

export default function EditDepotView({ 
  token, 
  userId, 
  baseUrl, 
  depot, 
  onCancel, 
  onUpdated 
}: {
  token: string;
  userId: string;
  baseUrl: string;
  depot: Depot;
  onCancel: () => void;
  onUpdated: (dep?: any) => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState<string>(depot.name || '');
  const [address, setAddress] = useState<string>(depot.address || '');
  const [latitude, setLatitude] = useState<number | null>(depot.latitude || null);
  const [longitude, setLongitude] = useState<number | null>(depot.longitude || null);
  const [center, setCenter] = useState<[number, number]>([
    depot.latitude || 46.0037, 
    depot.longitude || 8.9511
  ]);
  const [zoom] = useState<number>(13);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [suggestions, setSuggestions] = useState<Array<{ label: string; lat: number; lon: number }>>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string>('');
  const searchTimer = useRef<number | null>(null);

  useEffect(() => {
    const q = searchQuery.trim();
    if (searchTimer.current) window.clearTimeout(searchTimer.current);
    if (q.length < 3) {
      setSuggestions([]);
      return;
    }
    searchTimer.current = window.setTimeout(async () => {
      try {
        const url = `https://api3.geo.admin.ch/rest/services/api/SearchServer?sr=4326&type=locations&origins=address&lang=en&searchText=${encodeURIComponent(q)}`;
        const res = await fetch(url);
        const data = await res.json();
        const results = Array.isArray(data?.results) ? data.results : [];
        const mapped = results.map((r: any) => {
          const attrs = r?.attrs || {};
          const lat = typeof attrs.lat === 'number' ? attrs.lat : (typeof attrs.y === 'number' ? attrs.y : null);
          const lon = typeof attrs.lon === 'number' ? attrs.lon : (typeof attrs.x === 'number' ? attrs.x : null);
          const label = stripHtml((attrs.label || r?.label || '').toString());
          return lat != null && lon != null ? { label, lat, lon } : null;
        }).filter(Boolean) as Array<{ label: string; lat: number; lon: number }>;
        setSuggestions(mapped.slice(0, 8));
      } catch {
        setSuggestions([]);
      }
    }, 300);
    return () => {
      if (searchTimer.current) window.clearTimeout(searchTimer.current);
    };
  }, [searchQuery]);

  function SetView({ center }: { center: [number, number] }) {
    const map = useMap();
    useEffect(() => {
      map.setView(center);
    }, [map, center]);
    return null;
  }

  function ClickCapture() {
    const map = useMap();
    useEffect(() => {
      function onClick(e: any) {
        const lat = e.latlng?.lat;
        const lon = e.latlng?.lng;
        if (typeof lat === 'number' && typeof lon === 'number') {
          setLatitude(lat);
          setLongitude(lon);
        }
      }
      (map as any).on('click', onClick);
      return () => {
        (map as any).off('click', onClick);
      };
    }, [map]);
    return null;
  }

  async function submit() {
    setError('');
    if (!token) { setError(t('createDepot.errors.loginFirst') as string); return; }
    if (!userId) { setError(t('createDepot.errors.selectAgency') as string); return; }
    if (!name.trim()) { setError(t('createDepot.errors.nameRequired') as string); return; }
    if (latitude != null && (latitude < -90 || latitude > 90)) { setError(t('createDepot.errors.latitudeRange') as string); return; }
    if (longitude != null && (longitude < -180 || longitude > 180)) { setError(t('createDepot.errors.longitudeRange') as string); return; }
    try {
      setLoading(true);
      const payload: any = { name: name.trim() };
      if (address.trim()) payload.address = address.trim();
      if (latitude != null) payload.latitude = latitude;
      if (longitude != null) payload.longitude = longitude;
      const res = await fetch(joinUrl(baseUrl, `/api/v1/user/depots/${encodeURIComponent(depot.id)}`), {
        method: 'PUT', 
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, 
        body: JSON.stringify(payload)
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const data = await res.json();
      onUpdated(data);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-medium">{t('depots.editTitle')}</h2>
        <button onClick={onCancel} className="px-3 py-2 rounded-lg bg-gray-100 hover:bg-gray-200 text-sm">{t('common.back')}</button>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div className="space-y-2">
          <div>
            <label className="block text-sm text-gray-700 mb-1">{t('createDepot.form.nameLabel')}</label>
            <input className="w-full px-3 py-2 border rounded-lg" value={name} onChange={(e) => setName(e.target.value)} placeholder={t('createDepot.form.namePlaceholder') as string} />
          </div>
          <div>
            <label className="block text-sm text-gray-700 mb-1">{t('createDepot.form.searchLabel')}</label>
            <input className="w-full px-3 py-2 border rounded-lg" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} placeholder={t('createDepot.form.searchPlaceholder') as string} />
            {suggestions.length > 0 && (
              <ul className="mt-1 max-h-48 overflow-auto border rounded-lg bg-white shadow text-sm">
                {suggestions.map((s, i) => (
                  <li key={`${s.label}-${i}`} className="px-3 py-2 hover:bg-gray-100 cursor-pointer" onMouseDown={(e) => {
                    e.preventDefault(); setAddress(s.label); setCenter([s.lat, s.lon]); setLatitude(s.lat); setLongitude(s.lon); setSuggestions([]); setSearchQuery(s.label);
                  }}>{s.label}</li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <label className="block text-sm text-gray-700 mb-1">{t('createDepot.form.addressLabel')}</label>
            <input className="w-full px-3 py-2 border rounded-lg" value={address} onChange={(e) => setAddress(e.target.value)} placeholder={t('createDepot.form.addressPlaceholder') as string} />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="block text-sm text-gray-700 mb-1">{t('createDepot.form.latitudeLabel')}</label>
              <input className="w-full px-3 py-2 border rounded-lg" value={latitude ?? ''} onChange={(e) => setLatitude(e.target.value ? parseFloat(e.target.value) : null)} placeholder={t('createDepot.form.latitudePlaceholder') as string} />
            </div>
            <div>
              <label className="block text-sm text-gray-700 mb-1">{t('createDepot.form.longitudeLabel')}</label>
              <input className="w-full px-3 py-2 border rounded-lg" value={longitude ?? ''} onChange={(e) => setLongitude(e.target.value ? parseFloat(e.target.value) : null)} placeholder={t('createDepot.form.longitudePlaceholder') as string} />
            </div>
          </div>
        </div>
        <div>
          <MapContainer {...({ className: 'w-full h-[380px] rounded border', center } as any)} zoom={zoom as any}>
            <TileLayer {...({ url: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', maxZoom: 19 } as any)} />
            <SetView center={center} />
            <ClickCapture />
            {latitude != null && longitude != null && <Marker position={[latitude, longitude] as any} />}
          </MapContainer>
          <div className="mt-1 text-xs text-gray-600">{t('createDepot.mapHelp')}</div>
        </div>
      </div>
      {error && <div className="text-sm text-red-600">{error}</div>}
      <div className="flex gap-2">
        <button onClick={submit} disabled={loading || !token || !userId || !name.trim()} className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90 disabled:opacity-50" style={{backgroundColor: '#74C244'}}>
          {loading ? t('depots.updating') : t('common.save')}
        </button>
        <button onClick={onCancel} className="px-3 py-2 rounded-lg bg-gray-100 hover:bg-gray-200 text-sm">{t('common.cancel')}</button>
      </div>
    </div>
  );
}
