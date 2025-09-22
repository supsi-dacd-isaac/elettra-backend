import { useTranslation } from 'react-i18next';
import Panel from '../ui/Panel';

type ShiftStructureItem = { id: string; trip_id: string; shift_id: string; sequence_number: number };
type ShiftRead = { id: string; name: string; bus_id?: string | null; structure: ShiftStructureItem[]; updated_at?: string };

export type SavedShiftsPanelProps = {
  shifts: ShiftRead[];
  loading?: boolean;
  search: string;
  onSearch: (value: string) => void;
  filter: 'all' | 'mine';
  onFilter: (value: 'all' | 'mine') => void;
  sort: 'updatedDesc' | 'nameAsc';
  onSort: (value: 'updatedDesc' | 'nameAsc') => void;
  onRefresh: () => void;
  onView?: (shift: ShiftRead) => void;
  onDelete?: (shift: ShiftRead) => void;
  summaryByShiftId?: Record<string, { fromStop: string; fromTime: string; toStop: string; toTime: string }>;
  summaryLoading?: Record<string, boolean>;
};

export default function SavedShiftsPanel(props: SavedShiftsPanelProps) {
  const { t } = useTranslation();
  const {
    shifts,
    loading,
    search,
    onSearch,
    filter,
    onFilter,
    sort,
    onSort,
    onRefresh,
    onView,
    onDelete,
    summaryByShiftId,
    summaryLoading,
  } = props;

  return (
    <Panel>
      <div className="flex items-center justify-between gap-2 mb-2">
        <h2 className="text-lg font-medium">{t('saved.title', 'Saved shifts')}</h2>
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            placeholder={t('common.search', 'Search') as any}
            className="px-3 py-2 border rounded-lg text-sm"
            aria-label={t('common.search', 'Search') as any}
          />
          <select
            className="px-3 py-2 border rounded-lg text-sm"
            value={filter}
            onChange={(e) => onFilter((e.target.value as 'all' | 'mine'))}
            aria-label={t('saved.filter.label', 'Filter') as any}
          >
            <option value="all">{t('saved.filter.all', 'All')}</option>
            <option value="mine">{t('saved.filter.mine', 'Mine')}</option>
          </select>
          <select
            className="px-3 py-2 border rounded-lg text-sm"
            value={sort}
            onChange={(e) => onSort((e.target.value as 'updatedDesc' | 'nameAsc'))}
            aria-label={t('saved.sort.label', 'Sort') as any}
          >
            <option value="updatedDesc">{t('saved.sort.updatedDesc', 'Updated ▼')}</option>
            <option value="nameAsc">{t('saved.sort.nameAsc', 'Name A→Z')}</option>
          </select>
          <button onClick={onRefresh} className="px-2 py-1 rounded text-white text-xs hover:opacity-90 disabled:opacity-50" style={{backgroundColor: '#6b7280'}}>
            {t('common.refresh')}
          </button>
        </div>
      </div>

      <div className="divide-y divide-neutral-100">
        {loading ? (
          <>
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="py-2 animate-pulse">
                <div className="h-5 bg-neutral-100 rounded w-1/3 mb-1" />
                <div className="h-4 bg-neutral-100 rounded w-1/4" />
              </div>
            ))}
          </>
        ) : shifts.length === 0 ? (
          <div className="py-4 text-sm text-gray-700 flex items-center justify-between">
            <div>{t('saved.empty', 'No saved shifts yet.')}</div>
            <button
              onClick={() => {
                const el = document.getElementById('planner-controls');
                if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
              }}
              className="px-3 py-2 rounded-lg text-white text-sm hover:opacity-90 disabled:opacity-50"
              style={{backgroundColor: '#002AA7'}}
            >
              {t('shifts.createButton')}
            </button>
          </div>
        ) : (
          shifts.map((s) => {
            const sum = summaryByShiftId?.[s.id];
            const sumLoading = summaryLoading?.[s.id];
            return (
              <div key={s.id} className="py-2 flex items-start justify-between gap-2">
                <div className="text-sm">
                  <div className="font-medium">{s.name}</div>
                  <div className="text-gray-600">{t('shifts.tripCount', { count: s.structure?.length || 0 })}</div>
                  <div className="text-gray-600">
                    {sumLoading ? (
                      <span className="text-xs text-gray-500">{t('common.loading')}</span>
                    ) : sum ? (
                      <>{t('shifts.summary', sum as any)}</>
                    ) : (
                      <span className="text-xs text-gray-500">{t('common.loading')}</span>
                    )}
                  </div>
                  {s.updated_at && (
                    <div className="text-xs text-gray-500">{t('saved.updated', { value: s.updated_at })}</div>
                  )}
                </div>
                <div className="flex gap-2">
                  {onView && (
                    <button
                      className="px-2 py-1 rounded bg-blue-600 text-white text-sm hover:bg-blue-700"
                      onClick={() => onView(s)}
                    >
                      {t('saved.view', 'View')}
                    </button>
                  )}
                  {onDelete && (
                    <button
                      className="px-2 py-1 rounded bg-red-600 text-white text-sm hover:bg-red-700"
                      onClick={() => onDelete(s)}
                    >
                      {t('common.delete')}
                    </button>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>
    </Panel>
  );
}


