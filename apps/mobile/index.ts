/**
 * Entry point. Explicit instead of `expo/AppEntry.js` because pnpm hoists `expo` to the
 * workspace root and AppEntry's `../../App` import would resolve outside the app.
 */
import { registerRootComponent } from "expo";

import App from "./App";

registerRootComponent(App);
