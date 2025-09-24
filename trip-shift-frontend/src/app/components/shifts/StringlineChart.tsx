import { useMemo } from 'react';
import type { Series, StopMeta } from './stringlineUtils';

export type StringlineChartProps = {
  stopOrder: string[];
  stopMeta: Record<string, StopMeta>;
  series: Series[];
  width?: number;
  height?: number;
  loading?: boolean;
  error?: string;
};

function formatTimeLabel(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h < 24) return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
  const d = Math.floor(h / 24);
  const hh = h % 24;
  return `D+${d} ${String(hh).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

export default function StringlineChart(props: StringlineChartProps) {
  const { stopOrder, stopMeta, series, width = 960, height = 520, loading, error } = props;

  const padding = { top: 20, right: 20, bottom: 40, left: 140 };
  const innerWidth = Math.max(0, width - padding.left - padding.right);
  const innerHeight = Math.max(0, height - padding.top - padding.bottom);

  const yTicks = useMemo(() => stopOrder.map((sid, i) => ({ sid, i, name: stopMeta[sid]?.name || sid })), [stopOrder, stopMeta]);

  const allTimes = useMemo(() => {
    const arr: number[] = [];
    for (const s of series) for (const p of s.points) arr.push(p.minutes);
    return arr;
  }, [series]);

  const tMin = useMemo(() => (allTimes.length ? Math.floor(Math.min(...allTimes) / 60) * 60 : 0), [allTimes]);
  const tMax = useMemo(() => (allTimes.length ? Math.ceil(Math.max(...allTimes) / 60) * 60 : 60), [allTimes]);

  const scaleX = (m: number) => padding.left + (tMax === tMin ? 0 : ((m - tMin) / (tMax - tMin)) * innerWidth);
  const scaleY = (idx: number) => padding.top + (stopOrder.length <= 1 ? innerHeight / 2 : (idx / (stopOrder.length - 1)) * innerHeight);

  const hours = useMemo(() => {
    const arr: number[] = [];
    for (let h = tMin; h <= tMax; h += 60) arr.push(h);
    return arr;
  }, [tMin, tMax]);

  const palette = ['#1f77b4','#ff7f0e','#2ca02c','#d62728','#9467bd','#8c564b','#e377c2','#7f7f7f','#bcbd22','#17becf'];

  return (
    <div className="w-full overflow-auto" role="img" aria-label="Stringline chart">
      <svg width={width} height={height}>
        {/* Background */}
        <rect x={0} y={0} width={width} height={height} fill="#ffffff" />
        {/* Vertical hour grid */}
        {hours.map((h, i) => (
          <g key={`vx-${i}`}>
            <line x1={scaleX(h)} y1={padding.top} x2={scaleX(h)} y2={height - padding.bottom} stroke="#e5e7eb" />
            <text x={scaleX(h)} y={height - padding.bottom + 16} textAnchor="middle" fontSize={11} fill="#374151">{formatTimeLabel(h)}</text>
          </g>
        ))}
        {/* Horizontal stop lines and labels */}
        {yTicks.map((t) => (
          <g key={`hy-${t.sid}`}>
            <line x1={padding.left} y1={scaleY(t.i)} x2={width - padding.right} y2={scaleY(t.i)} stroke="#f3f4f6" />
            <text x={padding.left - 8} y={scaleY(t.i) + 4} textAnchor="end" fontSize={12} fill="#374151">{t.name}</text>
          </g>
        ))}
        {/* Series */}
        {series.map((s, si) => {
          const color = s.kind === 'depot' ? '#6b7280' : s.kind === 'transfer' ? '#a855f7' : palette[si % palette.length];
          // No filtering; we guarantee stopOrder contains all series stops upstream
          const pts = s.points.map((p) => ({
            x: scaleX(p.minutes),
            y: scaleY(stopOrder.indexOf(p.stopId)),
            arrivalHHMM: p.arrivalHHMM,
            departureHHMM: p.departureHHMM,
          }));
          if (pts.length < 2) return null;
          const d = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x},${p.y}`).join(' ');
          return (
            <g key={s.id}>
              <path d={d} stroke={color} strokeWidth={s.kind === 'trip' || !s.kind ? 2 : 1.5} strokeDasharray={s.kind && s.kind !== 'trip' ? '6 3' : undefined} fill="none" />
              {pts.map((p, idx) => {
                const labelParts: string[] = [];
                if (p.departureHHMM) labelParts.push(`Dep: ${p.departureHHMM}`);
                if (p.arrivalHHMM && p.arrivalHHMM !== p.departureHHMM) labelParts.push(`Arr: ${p.arrivalHHMM}`);
                const title = labelParts.length ? labelParts.join(' \u2022 ') : undefined;
                return (
                  <g key={`${s.id}-marker-${idx}`}>
                    <circle cx={p.x} cy={p.y} r={3}
                      fill={color}
                      stroke="#ffffff"
                      strokeWidth={1.2}
                    >
                      {title && <title>{title}</title>}
                    </circle>
                  </g>
                );
              })}
            </g>
          );
        })}
        {/* Axes titles */}
        <text x={padding.left + innerWidth / 2} y={16} textAnchor="middle" fontSize={12} fill="#111827">Time vs Stops</text>
        <text x={padding.left + innerWidth / 2} y={height - 6} textAnchor="middle" fontSize={12} fill="#6b7280">Time</text>
      </svg>
      {loading && <div className="text-sm text-gray-600 mt-1">Loading chart data…</div>}
      {error && <div className="text-sm text-red-600 mt-1">{error}</div>}
    </div>
  );
}


