// Metro config for pnpm monorepo + Expo SDK 51 web.
// Ensures Metro can resolve deps hoisted to the workspace root.
const { getDefaultConfig } = require("expo/metro-config");
const path = require("path");

const projectRoot = __dirname;
const workspaceRoot = path.resolve(projectRoot, "../..");

const config = getDefaultConfig(projectRoot);

config.watchFolders = [workspaceRoot];
config.resolver.nodeModulesPaths = [
  path.resolve(projectRoot, "node_modules"),
  path.resolve(workspaceRoot, "node_modules"),
  // pnpm's virtual hoisted store. With the isolated node-linker, a package's own
  // transitive deps live next to it under node_modules/.pnpm/<pkg>/node_modules,
  // NOT in apps/mobile/node_modules — but Metro searches this flat list instead
  // of walking up from the requiring file, so e.g. supabase-js's
  // require("@supabase/functions-js") only resolves if this directory is listed.
  path.resolve(workspaceRoot, "node_modules/.pnpm/node_modules"),
];
// pnpm uses symlinks; Expo 51's Metro supports this.
config.resolver.unstable_enableSymlinks = true;
config.resolver.unstable_enablePackageExports = true;

module.exports = config;
