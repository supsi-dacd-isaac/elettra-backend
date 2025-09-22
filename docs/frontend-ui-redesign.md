## Trip Shift Planner - UI Redesign Spec (Shifts/User)

Scope
- Keep Available trips unchanged.
- Redesign: Header user affordances, Saved Shifts (top), Planner Controls (left), Selected Shift (bottom).
- Improve hierarchy, spacing, and consistency without altering core flows.

Goals
- Clear separation of sections: Saved Shifts, Planner Controls, Available Trips, Selected Shift
- Professional look: consistent spacing, cards, typography, and color usage
- Better guidance during shift creation; unobtrusive, accessible feedback

Layout (Desktop ≥1024px)
- Top band (full-width): Saved Shifts
- Middle grid: 3 columns
  - Left: Planner Controls (sticky on scroll)
  - Center/Right: Available Trips (UNCHANGED)
- Bottom band (full-width): Selected Shift

Layout (Mobile/Tablet)
- Vertical stacking: Saved Shifts → Planner Controls (accordion) → Available Trips → Selected Shift
- Keep primary CTAs accessible; use collapsible panels to reduce scroll

Figma-like Sketches (ASCII)

Desktop overview
```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Header: [Logo + Title]    [Nav: Shifts | Depots | Fleet …]    [Lang] [User] │
├──────────────────────────────────────────────────────────────────────────────┤
│ Saved Shifts (Card)  [Search] [Filter: All|Mine] [Sort: Updated▼] [Refresh] │
│ ──────────────────────────────────────────────────────────────────────────── │
│  Name          From→To Summary             Trips   Updated          Actions  │
│  Morning A     Depot A 05:26 → Depot A…    8       2025-09-22 11:06  [View] │
│  …                                                                           │
└──────────────────────────────────────────────────────────────────────────────┘
┌───────────────┬──────────────────────────────────────────────────────────────┐
│ Planner Ctrls │  Available Trips (UNCHANGED)                                 │
│ (Sticky)      │                                                              │
│ ┌───────────┐ │  [Available trips list/cards, pagination, same as now]      │
│ │ Filters   │ │                                                              │
│ └───────────┘ │                                                              │
│ ┌───────────┐ │                                                              │
│ │ Create    │ │                                                              │
│ │ Shift     │ │                                                              │
│ └───────────┘ │                                                              │
│ ┌───────────┐ │                                                              │
│ │ Route/Day │ │                                                              │
│ └───────────┘ │                                                              │
│ ┌───────────┐ │                                                              │
│ │ Depot     │ │                                                              │
│ │ Flow      │ │                                                              │
│ └───────────┘ │                                                              │
└───────────────┴──────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────────────────┐
│ Selected Shift (Card)  [In progress: Shift Name] [Bus] [Valid/Err] [Save]    │
│ ──────────────────────────────────────────────────────────────────────────── │
│  [Leave Depot] → [Trip 1] → [Transfer] → [Trip 2] → … → [Return Depot]       │
│  (each chip has times/stops, route badge, remove/move icons)                 │
└──────────────────────────────────────────────────────────────────────────────┘
```

Mobile overview
```
Header (condensed) → Saved Shifts (card) → Planner Controls (accordion) →
Available Trips (unchanged) → Selected Shift (timeline stacked)
```

Header & Nav
- Keep existing layout
- Replace Logout button with a user dropdown: name, email, role, agency, link to /user, Logout
- Language selector: compact (icon + code), or keep existing if preferred

Saved Shifts (Top)
- Card with header and content:
  - Header: Title, Search input, Filter (All/Mine), Sort (Updated desc), Refresh
  - Content: list/table-like with columns:
    - Name (text)
    - From→To (summary e.g., "Depot X 05:26 → Depot X 08:10")
    - Trips (number)
    - Updated (datetime)
    - Actions: View (primary), Delete (icon)
- States
  - Loading: 5 skeleton rows
  - Empty: friendly message + "Create shift" CTA (scrolls to Planner Controls)
- Tailwind hints
  - Card: `bg-white border border-neutral-200 rounded-xl shadow-sm p-3 md:p-4`
  - Header row: `flex items-center justify-between gap-2`
  - Table body: `divide-y divide-neutral-100`
- i18n
  - `saved.title`, `common.search`, `saved.filter.all`, `saved.filter.mine`, `saved.sort.updatedDesc`,
    `saved.empty`, `saved.view`, `saved.updated`

Planner Controls (Left, grouped as accordion)
1) Filters Panel (existing)
   - Keep toggles; ensure consistent alignment and helper text sizing
2) Shift Creation Panel
   - Before start: CTA "Create shift" (primary)
   - After start: badge "In progress: <name>" and a secondary Cancel button
3) Route/Day Panel
   - Route select (disabled if no agencyId); show loading helper while routes load
   - Day select (always available after starting shift)
   - Helper: "Select agency on User page" when blocked
4) Depot Flow Panel
   - Two inline controls with icons + labels
     - Leave depot: [Depot select] [Time HH:MM] [Set/Change]
     - Return depot: [Depot select] [Time HH:MM] [Set/Change] (disabled until at least one trip selected)
   - After setting, show short summaries under each control

Selected Shift (Bottom)
- Timeline-style chips in order:
  - Leave Depot → Trip 1 → [Transfer?] → … → Return Depot
- Each chip shows time(s), stop(s), and (for trips) a route badge
- Inline actions on chips: Remove (X), Move up/down
- Sticky save bar within the card top:
  - Name (readonly), Bus (readonly), Validation chip (OK/Error), Save button
- Validation logic
  - Require Leave and Return depot legs; Return time > last arrival
  - Disable Save with tooltip when invalid

Visual tokens
- Colors: Primary `#002AA7`, Success `#74C244`, Neutral (Tailwind scale 50–900)
- Fonts: text-xs 12, sm 14, base 16, lg 18, xl 20; consistent line-heights
- Spacing: 4/8/12/16/24/32; panels use `p-3 md:p-4`; gaps 8–12

Buttons & Controls
- Primary: brand blue, text-white, hover opacity 90, disabled opacity 50
- Secondary: neutral bg `bg-neutral-100 hover:bg-neutral-200`
- Destructive: `bg-red-600 hover:bg-red-700`
- Icon-only buttons use accessible `aria-label`

Feedback & States
- Replace `alert()` with non-blocking toasts for success/error
- Inline errors near controls, small helper texts for constraints
- Show spinners in panel headers when loading; skeletons for Saved Shifts

Accessibility
- All interactive elements keyboard-friendly with visible focus rings
- Color contrast AA+; tooltips not the sole information channel
- Proper `aria-expanded` for collapsibles; `aria-live` for toasts

i18n Keys (additions)
- Saved Shifts: `saved.title`, `saved.filter.all`, `saved.filter.mine`, `saved.sort.updatedDesc`, `saved.view`, `saved.updated`, `saved.empty`
- Planner: `planner.inProgress`, `planner.cancel`, `planner.routeHelperSelectAgency`, `planner.routesLoading`
- Depot: `depot.leave.label`, `depot.return.label`, `depot.select`, `depot.time`, `depot.change`, `depot.summaryLeave`, `depot.summaryReturn`
- Selected: `selected.timeline.leave`, `selected.timeline.transfer`, `selected.timeline.return`, `selected.validation.ok`, `selected.validation.error`

Componentization Plan
- `SavedShiftsPanel`
  - Props: `{ shifts, loading, onSearch, filter, sort, onRefresh, onView, onDelete }`
- `PlannerAccordion`
  - Generic container for collapsibles
  - Children: `FiltersPanel`, `ShiftCreationPanel`, `RouteDayPanel`, `DepotFlowPanel`
- `SelectedShiftTimeline`
  - Props: `{ items, onRemove, onMoveUp, onMoveDown, validState, onSave }`

Acceptance Criteria
- Saved Shifts: searchable, filterable (All/Mine), sortable by Updated desc; shows Name, From→To, Trips, Updated, Actions; empty state with CTA
- Planner Controls: grouped panels guide next steps; clear disabled states and helpers
- Depot Flow: two inline controls with set/change and concise summaries
- Selected Shift: timeline chips with inline manage actions; sticky save strip with validation chip
- Available Trips remain visually and functionally unchanged

Out-of-Scope (for now)
- Any change to Available Trips list/cards and interactions
- Deep theming; later we can extract tokens into a dedicated theme file

Implementation Sequence (recommended)
1) Saved Shifts panel: header controls, list layout, skeleton/empty states
2) Planner Controls: accordion grouping + refined Shift Creation panel
3) Depot Flow: inline two-control design with summaries and helpers
4) Selected Shift: timeline chips + sticky save strip with validation chip


