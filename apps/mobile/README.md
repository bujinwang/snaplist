# apps/mobile

Expo SDK 51 / React Native scaffold. Owned by the **mobile** role — E6 in `docs/PLAN.md`.

**Status: E6-T1 done** — app shell, Capture/Review router, anonymous Supabase auth (mock fallback),
and the analytics emitter (frozen vocabulary) are implemented. `Capture` and `Review` screens are
placeholders; E6-T2/T3 build the real flows. The web landing page is a separate scaffold in
`apps/web` (E7-T5).

Run from the repo root (pnpm workspace):

```bash
pnpm install                          # once; note registry may refuse tarballs — see HANDOFF-mobile.md
pnpm --filter mobile lint             # tsc --noEmit, the gate
pnpm --filter mobile web              # renders in a browser, useful for design review
pnpm --filter mobile start            # expo dev server (needs a device or simulator)
```

`API_BASE` comes from `app.json` `extra` via `expo-constants`, never a hardcoded string. With
`API_BASE` unset the app runs in **mock mode**: every screen renders with fixture data and analytics
is a no-op sender, so design review needs no backend.

`index.ts` is the entry point instead of `expo/AppEntry.js` because pnpm hoists `expo` to the
workspace root and AppEntry's `../../App` import would resolve outside the app.
