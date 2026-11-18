# UI / UX Specification — XCOM 2 Campaign Planner

Status: Draft v1. Dark-mode only (`NFR-9`). Desktop-first, reasonably responsive. XCOM-inspired but not a copy of the game's UI. All business logic server-side; JS is progressive enhancement only (`ARCHITECTURE.md` §2, `ADR-0011`).

## 1. Visual language

- **Palette** (CSS custom properties in `static/css/theme.css`, all overridable for a future second theme per `NFR-9`): near-black charcoal backgrounds (`--bg-0: #0b0e11`, `--bg-1: #12161b`, `--bg-2: #1a2027`), desaturated steel-blue panels (`--panel: #1e2630`), amber/gold accent for primary actions and "ready" states (`--accent: #d99a2b`), cold cyan for informational/selected states (`--info: #3fb6c9`), muted red for blocked/danger (`--danger: #c04b4b`), olive/green for completed/owned (`--success: #6c9a3f`). Typography: a condensed, slightly military/industrial sans (e.g. system UI fallback stack with a bundled open-licensed display face for headings only — no copyrighted game font).
- **Iconography**: flat, geometric line icons per object-type category (own SVGs, not extracted from the game — `ADR-0008`). Every object type has one fallback glyph; every object's card/detail shows an image when one exists, else the type glyph — never a blank box (`FR-CAT-4`, `DOMAIN_MODEL.md` §8).
- **Texture**: subtle angular/hex-panel background accents on empty states and headers, chamfered (not fully rounded) card corners, thin 1px high-contrast borders — evokes XCOM's Avenger-bridge UI without tracing it.
- **Motion**: minimal — short (150ms) fade/slide for drawers and modals, no decorative animation that blocks interaction.

## 2. Layout shell

```
┌─────────────┬──────────────────────────────────────────────┐
│             │  Top bar: Campaign switcher ▾   [Search]   ⚙ │
│  Left Nav   ├──────────────────────────────────────────────┤
│ (persistent)│                                                │
│             │                Main content area               │
│  Dashboard  │           (route-specific, scrollable)         │
│  Catalog    │                                                │
│  Plans      │                                                │
│  Soldiers   │                                                │
│  Events     │                                                │
│  Content    │                                                │
│  Packs      │                                                │
│  ─────────  │                                                │
│  Settings   │                                                │
└─────────────┴──────────────────────────────────────────────┘
```

- **Left nav** (persistent, collapsible to icon-only rail on narrower viewports, requirement: "persistent left-hand navigation"): Dashboard, Catalog, Plans, Soldiers, Events, Content Packs, Settings. Active campaign context is implicit in every nav link once a campaign is selected; "Content Packs" and global catalog browsing (no campaign filter) remain reachable without one.
- **Top bar**: campaign switcher dropdown (shows current campaign name + difficulty/profile badge; "+ New Campaign" entry; list of other campaigns with quick-switch); global search (objects by name, campaign-scoped by default, toggle to search the whole catalog); settings gear (asset library, strategy profiles, backups).
- **Breakpoints**: desktop-first design target ≥1280px; nav collapses to icon rail <1024px; card grids reflow from 4/3/2 columns down to 1 column <640px; no dedicated mobile navigation pattern in v1 (non-goal, `PRODUCT_SPEC.md` §9).

## 3. Screens

### 3.1 Dashboard (`/campaigns/<id>`)
- Header: campaign name, profile/difficulty/iron-man badges, enabled-sources chips.
- Progression summary cards: counts by status (researched, in-progress, owned items, facilities built) — generic aggregates over `campaign_object_state`, not type-specific hardcoding.
- "Immediate priorities" panel: top N `ready` plan items by priority (`FR-PLN-4`), with strategy-profile badges where a recommendation boosted them.
- "Blocked" panel: plan items currently blocked, each with a one-line "missing: X, Y" summary, click → opens the requirement-tree drawer (§3.5).
- Recent events timeline (last 10 `campaign_events`), "View all" → Events screen.

### 3.2 Catalog browser (`/catalog`, global and campaign-filtered — Phase 3 implemented without the campaign-id path segment; see note below)
- Left filter rail (within content area, distinct from the persistent app nav): object type checklist, source checklist (only campaign-enabled sources shown by default, toggle "show all sources"), tag multi-select, text search box, availability filter (locked/available/in-progress/completed/owned).
- Card grid, one card per object: image/icon (always present) + name (requirement: "Object selections should display both image/icon and name whenever an image exists" — applied to every card/list/select everywhere in the app, not just here), type badge, tier badge if set, availability badge (colour-coded per §1 palette), source chip.
- Card click → **drawer** (right-side slide-in, not full navigation) with: full description, attributes table (rendered generically from `catalog_attribute_definitions.display_hint`), images gallery, "Requires" and "Unlocks" relationship lists (clickable, chain to further drawers), campaign-state quick-edit (status dropdown, quantity stepper, notes), provenance footer (source, template key, builtin vs. user-authored badge).
- "+ Add custom object" button → modal form (type picker, name, description, attribute form driven by the chosen type's definitions, image upload, initial relationships) creating a row in the campaign's (or a chosen) user source (`FR-CAT-7`).

**Phase 3 implementation note**: the catalog lives at a single, non-campaign-scoped route `/catalog` (not `/campaigns/<id>/catalog`) — the active campaign (if any, from the session-based context established in Phase 2) implicitly filters the source list, rather than the campaign id being part of the URL. This was a deliberate simplification: it lets the global catalog-admin view ("no active campaign") and the campaign-filtered view share one route/template instead of two, and matches how every other Phase 2 screen already reads the active campaign from session context rather than the URL. Object detail is a **full page** (`/catalog/objects/<id>`), not a slide-in drawer — the drawer pattern (URL-reflected via `?drawer=`, deep-linkable, `UI_UX_SPEC.md` §4) is deferred; a full page is simpler to get right first (correct data, correct relationship CRUD, correct image upload) and the route shape (`catalog.detail`) does not preclude later wrapping it in a drawer-on-top-of-the-grid presentation without changing the URL. Availability badges/campaign-state quick-edit are not present yet — they depend on `campaign_object_state`, which belongs to the future state-tracking phase (`IMPLEMENTATION_PLAN.md` Phase 6).

### 3.3 Object detail drawer (reusable component)
Used from Catalog, Plans, Soldiers wherever an object reference is clickable. Contents as in §3.2's drawer description; includes a collapsible **requirement tree** (`FR-PLN-2`): recursive AND/OR grouped list, satisfied nodes shown muted/checked, unsatisfied nodes highlighted in `--danger`, each node itself expandable to its own requirements.

**Phase 3 implementation note**: the requirement-tree view does not exist yet (it needs the future requirements resolver, `ARCHITECTURE.md` §6); the Phase 3 object detail page shows the raw relationship graph (outbound/inbound lists with remove actions) and an Alpine-driven image+name object picker for adding new relationships, in place of the not-yet-built drawer/tree.

### 3.10 Avenger Facility Planner (`/campaigns/<id>/avenger/`, Phase 5, `ADR-0020`)
Signature "command interface" page, reached from the left nav's **Avenger** entry (enabled when a campaign is active).

- **Console**: dark technical panel with cyan corner brackets and a faint scan-line texture; a CSS Grid of **3 columns × 4 rows** (never an HTML table; `role="grid"`, each tile a link with an accessible name). Tiles are 16:10, remain usable at laptop widths, and the side panel stacks below under 1100px.
- **Tile anatomy**: coordinate (top-left), room-state chip, status badge (top-right), blocked warning glyph, artwork filling the tile (facility image if the catalog object has one, else a tinted per-type glyph whose hue is derived from the catalog object), name + build-state/power overlay at the bottom.
- **Status treatments** (colour *and* text, `NFR-6`): unconfigured = dashed grey + hatch + "EMPTY (unconfigured)"; planned = dashed border + dimmed art; planned-blocked = amber dashed border + ⚠; constructing = amber border + animated build bar; built = solid tinted border + glow; uncleared hazard room = diagonal hazard hatching. Hover lifts the tile with a cyan glow; selection pulses; `prefers-reduced-motion` disables animation.
- **Unconfigured campaign**: banner "Layout not configured yet" with a primary *Configure Starting Avenger* action; all 12 tiles show the EMPTY placeholder.
- **Configure Starting Avenger** (`?mode=configure`): the same grid becomes a draft editor. Selecting a cell shows the room-state card gallery (image/glyph + name + effect, populated from the catalog); choosing one sets the cell and auto-advances to the next so the player can click through all 12. Buttons: **Save Layout** (atomic POST), **Cancel** (confirms if the draft is dirty), **Reset to EMPTY** (draft only). A counter shows "n / 12 cells set".
- **Cell modal** (htmx fragment, `Esc`/backdrop dismissible, also a no-JS full page via `?cell=R2C3`): sections **Cell** (coordinate, room state + change control), **Current** (facility + build state if constructing/built), **Plan** (planned facility, status, blockers with per-blocker "Mark … complete" actions, build-state buttons, Clear facility) and **Available facilities** — a card gallery (art, name, purpose, power, cost, build time, prerequisite state, Unique/Can-duplicate tag; unique facilities already placed are disabled with the reason). Selecting a card enables **Save facility** with a *Save as* Planned/Constructing/Built choice. A *Details* link under each card loads the secondary detail panel (description, cost, power, build time, staffing, upgrades, prerequisites).
- **Summary panel**: counts, power (catalog figures + explicit "not tracked" caveat), unresolved prerequisites list linking to the owning cell.
- **Errors** (duplicate unique facility, invalid facility) re-render the modal with the message; the plan is untouched.

### 3.4 Plans (`/campaigns/<id>/plans`)
- Plan selector (a campaign can have multiple named plans), "+ New Plan".
- Plan items as a reorderable (drag-and-drop, progressive JS; falls back to up/down buttons without JS) list: object image+name, action hint text, priority stepper, status badge (`blocked`/`ready`/`done`), manual-dependency indicator.
- "+ Add item" → modal: object picker (same card-with-image-and-name component as Catalog, searchable), action hint, rationale, priority, optional "depends on" picker scoped to the same plan.
- Clicking a blocked item's badge opens the requirement-tree drawer (§3.3) pre-scoped to that object.

### 3.5 Requirement tree drawer
Shared component (§3.3) also reachable standalone from Dashboard's "Blocked" panel — same drawer, different entry point.

### 3.6 Soldiers (`/campaigns/<id>/soldiers`)
- Roster grid: soldier portrait (asset or class-default glyph) + name, class badge, rank, status chip.
- Soldier detail drawer: current loadout (slots with item image+name, empty-slot placeholder), planned loadout (toggle "current/planned" view), class-can-equip warnings (non-blocking, amber) if a planned item fails `class_can_equip` (`FR-PLN-6`).
- "+ Add soldier" modal: name, class picker (image+name card list), portrait upload.

### 3.7 Events (`/campaigns/<id>/events`)
Simple reverse-chronological timeline list, filterable by event type; "+ Log event" modal (type, linked object optional, summary, in-game label free text).

### 3.8 Content Packs (`/content`)
- Not campaign-scoped (global). Card list of imported `content_sources`: name, kind badge, version, workshop-id link if present, enabled-campaigns count.
- "Import pack" action (points at a `content/packs/<slug>` directory or an uploaded archive in a later phase) → runs dry-run validation, shows a diagnostics report (new/updated/retired counts, errors) in a modal before confirming the real import (`FR-SRC-7`).
- Per-source detail drawer: dependencies (`requires`/`extends`/`conflicts`), object-type/relationship-type contributions, object count by type.

### 3.9 Campaign creation / settings (`/campaigns/new`, `/campaigns/<id>/settings`)
- New campaign modal/page: name, **game profile** picker (cards: "WotC Vanilla", "LWotC", "Custom" — selecting a profile pre-checks its default sources), difficulty, iron-man toggle, notes.
- Settings page: enabled content sources with drag-reorderable precedence list (`campaign_content_sources.precedence`), strategy profiles toggles (visually distinct section, labelled "Community Strategy (opinions, not game rules)" per `ADR-0009`), duplicate/archive/delete campaign actions (delete requires a typed-name confirmation modal — destructive action safeguard).

## 4. Interaction patterns

- **Modals**: short, single-purpose forms and destructive confirmations (create campaign, add soldier, delete campaign, import-pack confirmation). Always keyboard-dismissible (`Esc`), focus-trapped (`NFR-6`).
- **Drawers**: anything that benefits from "glance without losing your place" — object detail, requirement tree, soldier detail, quick-edit of campaign state from a card. Slide in from the right, same focus-trap/`Esc` rules, URL-reflected via query param (`?drawer=object:123`) so drawers are deep-linkable/shareable and survive refresh.
- **Object pickers** (used in plan items, soldier loadouts, relationship authoring): consistent searchable card-list component, always image+name, reused everywhere an object reference is chosen — one Jinja macro / one JS module, not reimplemented per screen.
- **Badges**: availability and status always rendered with both colour and a text label (never colour-only) for accessibility (`NFR-6`).
- **Empty states**: every list/grid has a designed empty state (icon + short copy + primary action), not a blank area — matters a lot here since a brand-new campaign starts with zero state everywhere.

## 5. Accessibility (`NFR-6`)

- All interactive elements reachable by keyboard (`Tab`/`Shift+Tab`), visible focus ring using `--info` outline.
- Modals/drawers trap focus and restore it to the triggering element on close.
- Colour contrast meets WCAG AA against the dark backgrounds (verified against the palette in §1 during implementation, documented in a contrast-check note under `static/css/theme.css`).
- Every image `<img>`/icon has `alt` text derived from the object's `name` (or type name for fallback glyphs).
- Status conveyed by icon/text + colour, never colour alone (§4).

## 6. Responsiveness

- Desktop-first (per constraint). Target primary resolution ≥1280×800.
- Card grids use CSS grid with `auto-fill`/`minmax` so column count degrades gracefully; nav collapses to an icon rail below 1024px; drawers become full-width sheets below 768px; no bespoke mobile nav/gestures in v1.

## 7. Component inventory (for implementation planning)

| Component | Used in |
|---|---|
| `ObjectCard` (image/icon + name + type badge + availability badge) | Catalog grid, object pickers, soldier roster, plan items |
| `ObjectDetailDrawer` | Catalog, Plans, Soldiers |
| `RequirementTree` | Object detail drawer, Dashboard blocked panel |
| `CampaignSwitcher` | Top bar |
| `SourcePrecedenceList` (drag-reorderable) | Campaign settings |
| `AttributeForm` (schema-driven from `catalog_attribute_definitions`) | Add/edit custom object modal |
| `StatusBadge` / `AvailabilityBadge` | everywhere object/plan-item status appears |
| `ConfirmDestructiveModal` (typed-name confirm) | Delete campaign/source |
| `Timeline` | Events, Dashboard recent events |

These map 1:1 to Jinja macros under `templates/_components/` plus a thin JS module per interactive one (`static/js/components/`), per `ARCHITECTURE.md` §2.
