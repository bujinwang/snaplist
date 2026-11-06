# HANDOFF — mobile (E6)

Owner: MOBILE ENGINEER (Expo / React Native / TypeScript), `apps/mobile/`.
Scope rule: stay in `apps/mobile/`. Reads `packages/shared` vocabulary and the API contract; does not touch backend code.

## E6-T1 — App shell, routing, Supabase anon auth, analytics emitter

**What works**
- App shell, gated behind anonymous sign-in (`AuthProvider`): `anon_<uuid>` on first launch when
  Supabase env is unset (mock mode), so the app is fully runnable with no backend.
- `API_BASE` comes from `expo-constants` (`app.json` `extra`); never hardcoded. When unset, every
  screen renders mock data.
- Analytics emitter (`src/lib/analytics.ts`) with the frozen vocabulary, including
  `subscription_started`. Dev loud-fail on malformed events; no-op/console in mock mode.

## E6-T2 — Capture (single item)

- Quad-guide overlay with corner ticks, mock camera preview, torch **toggle** (real torch
  deferred — `expo-camera` not installed), and a "Shoot the back" prompt.
- One tap simulates capture → upload → job → review with mock data (no backend).
- Emits `upload_started` with `capture_mode: "single"`.

## E6-T3 — Review + attribute editing

- Side-by-side original vs prettified (mock colour blocks), Approve / Retry / Remove-bg-waitlist,
  horizontal swipe between items.
- Editable attribute chips for category / colour / pattern / material with a subtle confidence
  bar; one-tap correct.
- Optimistic PATCH + rollback: a "Simulate offline (next edit fails)" switch makes the next PATCH
  fail, the UI reverts to the previous attributes, and the §6 `internal` copy is shown (never a
  `reason_code`).
- Emits `prettify_approved` (with `job_latency_ms`, `prettify_latency_ms`, `attrs_latency_ms`) and
  `attributes_edited` (with `fields_touched` — reading counts as touching — `edited_field_count`,
  and `field_names` **without values**).

## E6-T4 — On-model + Export

- **On-model**: Pro paywall (`paywall_impression` emitted once by `<PaywallCard>` with a
  deterministic mock variant — the real variant is server-assigned, E8-T4), then a **one-time
  consent gate that quotes `docs/templates/consent-copy.md` v1.0 verbatim** (the vton request
  cannot be made until acknowledged), a model-card picker, and a slot showing
  not_requested / rendering / ready. It is its own route, so it **never blocks review** (ADR-004).
- **Export**: marketplace picker (eBay / Poshmark / Vinted / copy), CSV row preview, and a
  copy/share action. Emits `export_clicked` (`export_latency_ms`) and `export_delivered`
  (`bytes`, `delivery_latency_ms`). Free tier is capped at 3 exports, then `export_limit` paywall.
- `subscription_started` carries `amount_cents: 4900` (Pro $49/mo, per BRIEF §Payments).

## E6-T5 — Local drafts + offline queue

- `DraftsProvider` is the single source of truth for items; every change is persisted.
- Persistence is lazy: `expo-file-system` → `localStorage` → in-memory (native modules are not
  installed). On mount, any `queued`/`running` item is resumed, so a force-quit mid-job recovers.
- The free-tier wall interacts with **drafts, not the camera**: the 4th capture is saved as
  `blocked_by_quota` and is never discarded.
- `Drafts` screen lists every persisted item, in-flight / quota-blocked counts, §6 failure copy,
  Retry, Remove, and Review.

## E6-T6 — Batch capture

- Single/Batch mode toggle; every tap enqueues its own upload with per-item progress.
- A per-item failure is a first-class row (which item, §6 copy, Retry) and never aborts the others;
  partial success is the normal state.
- `upload_started` carries `capture_mode: "batch"` and `batch_index`.

## Verification output (real)

```
$ cd apps/mobile && npx tsc --noEmit
(clean — exit 0, no output)
```

```
$ npx expo export --platform ios
(node:86630) [DEP0040] DeprecationWarning: The `punycode` module is deprecated. ...
Starting Metro Bundler
iOS Bundled 30632ms index.ts (573 modules)

Exporting , bundle, for ,: 1  ios
_expo/static/js/ios/index-fbd84db8057f8b73845b6b7a88c9a27c.hbc (,) 1.57 MB

Exporting , file,: 1
metadata.json (,) 142 B

App exported to: dist
```

`apps/mobile/dist/` is gitignored (`.gitignore:14`).

## What is stubbed / deferred (honest)

- **`expo-camera` is not installed** (registry mirror refused tarballs): the camera preview is a
  styled mock and the torch is a state toggle. Real capture wiring is deferred until the package
  can be installed. This is why screenshots are "code-renderable" but not device-captured.
- **`expo-file-system` / `expo-sqlite` are not installed**: persistence falls back to
  `localStorage` (web) / in-memory. A native build must swap in the real backends.
- **Supabase and `@snaplist/shared` are not installed**: both are lazy-`require`d; the analytics
  vocabulary is a local typed mirror of `packages/shared/src/analytics.ts` (must not drift).
- **`paywall_variant` is a deterministic mock** derived from a hash of the subject. The API must
  return the sticky server-assigned variant (E8-T4).
- **On-model renders are mock**: `requestOnModel` only flips local state. Real renders are BLOCKED
  on `FASHN_API_KEY` / `MODAL_TOKEN` — see `packages/vision/BLOCKED.md`.

## 10-line summary

1. E6 is built end-to-end in mock mode: Capture → Review/Attributes → On-model → Export, plus Drafts.
2. `tsc --noEmit` is clean; no `any` in the app.
3. `expo export --platform ios` succeeds (573 modules, 1.57 MB bundle).
4. Every screen renders with mock data when `API_BASE` is unset.
5. All frozen analytics events are emitted from the screen that owns them.
6. On-model quotes the E9 consent block verbatim and gates the render on acknowledgement.
7. Offline queue persists and resumes; the 4th capture is saved, not lost.
8. Batch is per-item with partial success and per-row retry.
9. Deferred: real camera/FS/SQLite native modules, Supabase client, server-assigned paywall variant.
10. Next: install `expo-camera` + `expo-file-system` + `supabase-js` when the registry recovers,
    then run `npx expo start` on a device.
