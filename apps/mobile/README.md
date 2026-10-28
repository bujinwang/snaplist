# apps/mobile

Expo SDK 51 / React Native scaffold. One screen (`App.tsx`) that renders "SnapList".

Owned by the **mobile** role — E6 in `docs/PLAN.md`. Nothing in this directory is
implemented yet beyond the entry point.

```bash
pnpm install                    # from the repo root, once
pnpm --filter mobile lint       # tsc --noEmit, the gate
pnpm --filter mobile start      # expo dev server (needs a device or simulator)
pnpm --filter mobile web        # renders in a browser, useful for design review
```

`index.ts` is the entry point instead of `expo/AppEntry.js` because pnpm hoists `expo`
into the root `node_modules`, which breaks AppEntry's relative `../../App` import.
