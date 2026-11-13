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
- Persistence is real and durable: `./storage.ts` statically imports `expo-file-system` (17.0.1) and
  writes the whole queue to a versioned JSON document in `documentDirectory` on every change;
  `localStorage` is the web fallback. On mount, any `queued`/`running` item is resumed, so a
  force-quit mid-job recovers. Deliberately one JSON document, not SQLite: the queue is one ordered
  array rewritten whole on each mutation, so a schema + a second native module buy nothing.
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
$ npx expo export --platform ios --output-dir /tmp/sn-doc-ios --clear
warning: Bundler cache is empty, rebuilding (this may take a minute)
iOS Bundled 27723ms index.ts (628 modules)

Exporting , bundle, for ,: 1  ios
_expo/static/js/ios/index-06de54fa6af3b4be1f492d8c7c2f3b5f.hbc (,) 2.17 MB

Exporting , file,: 1
metadata.json (,) 142 B

App exported to: /tmp/sn-doc-ios
```

```
$ npx expo export --platform web --output-dir /tmp/sn-doc-web --clear
warning: Bundler cache is empty, rebuilding (this may take a minute)
Web Bundled 19411ms index.ts (254 modules)

Exporting , bundle, for ,: 1  web
_expo/static/js/web/index-d6681152cdbca8b2a65d19b422608824.js (,) 647 kB

Exporting , file,: 2 s
index.html (,) 1.18 kB
metadata.json (,) 49 B

App exported to: /tmp/sn-doc-web
```

Both are measured with `--clear`, i.e. from a cold cache — and **the module count is not a
fixed number**: it drifts with cache state and with the tree (warm-cache web runs have been seen
across 241–254 modules for the same 647 kB bundle). Quote the byte figure, which is stable; treat
the count as indicative. Before the `metro.config.js` resolver fix the iOS command failed with
`Unable to resolve module @supabase/functions-js` — that message is the reproducible part; the
count it reached before failing drifts too (560–564). The older 573-module / 1.57 MB figures
predate both the static supabase import and expo-file-system.

**The Hermes `.hbc` is not byte-reproducible, and build identity must not pretend it is.**
Exporting an identical source tree twice has produced the same file name, the same
`index-<hash>` stamp and the same size while the **file md5 differed**. Mobile build identity
here therefore pins the **filename hash** (`index-<hash>` — what `metadata.json` and the
native build reference) and never the bytes. The hash is **commit-scoped**: it changes whenever
the source changes (this run: `06de54fa…` at `e2d12a6`), so quote it with the commit; the durable
artifact is the byte size. Do not diff `.hbc` md5s between builds and call the difference a
regression, and do not use them as a cache key.

`apps/mobile/dist/` is gitignored (`.gitignore:14`).

## What is stubbed / deferred (honest)

- **`expo-camera` is not installed** (registry mirror refused tarballs): the camera preview is a
  styled mock and the torch is a state toggle. Real capture wiring is deferred until the package
  can be installed. This is why screenshots are "code-renderable" but not device-captured.
- **`expo-file-system` is installed** (17.0.1): the draft queue really does survive a force-quit.
  `expo-sqlite` is intentionally NOT added — see E6-T5 above. `localStorage` covers web.
- **Supabase is installed and imported statically** (`@supabase/supabase-js` ^2.119.0 +
  `expo-constants` 16.0.0): the client is constructed only when `hasSupabase` is true, so mock mode is
  unchanged when env is absent. `@snaplist/shared` is still NOT imported — the analytics vocabulary
  is a local typed mirror of `packages/shared/src/analytics.ts` (must not drift), because CI runs
  `lint_ts` before `test_js` builds `packages/shared/dist` and `dist/` is gitignored.
- **`paywall_variant` is a deterministic mock** derived from a hash of the subject. The API must
  return the sticky server-assigned variant (E8-T4).
- **On-model renders are mock**: `requestOnModel` only flips local state. Real renders are BLOCKED
  on `FASHN_API_KEY` / `MODAL_TOKEN` — see `packages/vision/BLOCKED.md`.

## 10-line summary

1. E6 is built end-to-end in mock mode: Capture → Review/Attributes → On-model → Export, plus Drafts.
2. `tsc --noEmit` is clean; no `any` in the app.
3. `expo export` succeeds for iOS (628 modules, 2.17 MB) and web (647 kB; module count drifts with cache).
4. Every screen renders with mock data when `API_BASE` is unset.
5. All frozen analytics events are emitted from the screen that owns them.
6. On-model quotes the E9 consent block verbatim and gates the render on acknowledgement.
7. Offline queue persists and resumes; the 4th capture is saved, not lost.
8. Batch is per-item with partial success and per-row retry.
9. Deferred: real camera native module, SQLite (not needed), server-assigned paywall variant.
10. Next: install `expo-camera` when the registry recovers, then run `npx expo start` on a device.
