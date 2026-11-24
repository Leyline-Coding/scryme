// Pure security policy for the desktop shell: which URLs may leave the app, where the window may
// navigate, which permissions a page may hold, and which environment variables reach the backend.
// No Electron imports, so `npm test` exercises it under plain Node.

const fs = require("node:fs");

const RELEASES_URL = "https://github.com/Leyline-Coding/scryme/releases";

function parseUrl(raw) {
  try {
    return new URL(String(raw));
  } catch {
    return null;
  }
}

// Only https: links are handed to the OS. Everything the UI links to off-site (Scryfall, GitHub,
// the WotC policy, deck source URLs) is https; anything else — file:, smb:, ms-*:, custom protocol
// handlers, plain http: — is refused rather than passed to shell.openExternal.
function isSafeExternalUrl(raw) {
  const url = parseUrl(raw);
  return Boolean(url && url.protocol === "https:" && url.hostname && !url.username && !url.password);
}

// The app's own origin is exactly http://127.0.0.1:<port> — the backend never serves the window
// from any other host name.
function appOriginFor(port) {
  return `http://127.0.0.1:${Number(port)}`;
}

function isAppUrl(raw, appOrigin) {
  if (!appOrigin) return false;
  const url = parseUrl(raw);
  return Boolean(url && url.protocol === "http:" && url.origin === appOrigin);
}

// Decision for a top-level navigation / redirect / window.open target:
//   "allow"    — the app's own origin
//   "external" — an https: URL; open it in the user's browser instead
//   "deny"     — everything else
function navigationDecision(raw, appOrigin) {
  if (isAppUrl(raw, appOrigin)) return "allow";
  if (isSafeExternalUrl(raw)) return "external";
  return "deny";
}

// Sub-frames may only show the app itself (or an empty about: document); never open the browser on
// a frame's behalf.
function frameNavigationAllowed(raw, appOrigin) {
  if (isAppUrl(raw, appOrigin)) return true;
  const url = parseUrl(raw);
  return Boolean(url && url.protocol === "about:" && (url.pathname === "blank" || url.pathname === "srcdoc"));
}

// Deny-by-default web permissions. The UI needs exactly two: copy buttons call
// navigator.clipboard.writeText (settings / pairing / share links) and saved-search + price alerts
// raise a Notification. Both are granted to the app's own origin only.
const ALLOWED_PERMISSIONS = new Set(["clipboard-sanitized-write", "notifications"]);

function permissionAllowed(permission, requestingUrl, appOrigin) {
  return ALLOWED_PERMISSIONS.has(permission) && isAppUrl(requestingUrl, appOrigin);
}

// SCRYME_* variables the desktop backend may inherit from the launching environment. These are
// documented user-facing knobs that do not weaken the local security posture. Everything else with
// the prefix is dropped — notably SCRYME_LAN_GUARD, SCRYME_READ_ONLY, SCRYME_DATABASE_URL / DB_*,
// SCRYME_DATA_DIR, SCRYME_PORT and SCRYME_SCRYFALL_* — so a stray export in a shell profile (say,
// from running the Docker build) cannot reconfigure the desktop app. Electron-only variables
// (SCRYME_DESKTOP_DATA_DIR, SCRYME_PYTHON) are read by main.js and not forwarded either.
const BACKEND_ENV_PASSTHROUGH = new Set([
  "SCRYME_LLM_BASE_URL",
  "SCRYME_LLM_API_KEY",
  "SCRYME_LLM_CHAT_MODEL",
  "SCRYME_LLM_EMBED_MODEL",
  "SCRYME_BACKUP_DIR",
  "SCRYME_BACKUP_KEEP",
  "SCRYME_BACKUP_INTERVAL_HOURS",
  "SCRYME_BACKUP_PASSPHRASE",
  "SCRYME_DEFAULT_CURRENCY",
  "SCRYME_DEFAULT_PRICE_SOURCE",
  "SCRYME_API_TOKEN",
]);

// Build the backend's environment: the parent's non-SCRYME variables (PATH, HOME, locale, proxy
// settings…), the allow-listed SCRYME_* ones, then the values the shell itself decides. The prefix
// match is case-insensitive because the backend's settings loader is (and Windows env names are).
function backendEnv(parentEnv, forced) {
  const env = {};
  for (const [key, value] of Object.entries(parentEnv || {})) {
    if (value === undefined) continue;
    const upper = key.toUpperCase();
    if (upper.startsWith("SCRYME_")) {
      if (BACKEND_ENV_PASSTHROUGH.has(upper)) env[upper] = value;
      continue;
    }
    env[key] = value;
  }
  return { ...env, ...forced };
}

// GitHub release page for a version reported by the update feed. The version string comes from the
// feed, so it is only used when it looks like a plain semver; otherwise link the release list.
function releaseUrlFor(version) {
  const v = String(version || "");
  if (/^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$/.test(v)) return `${RELEASES_URL}/tag/v${v}`;
  return `${RELEASES_URL}/latest`;
}

// Whether LAN sharing is switched on, read from the backend's lan.json (same shape and same
// "anything unreadable means off" rule as backend/src/lan.py: lan_state()).
function readLanEnabled(file) {
  try {
    const data = JSON.parse(fs.readFileSync(file, "utf8"));
    return Boolean(data && typeof data === "object" && data.enabled);
  } catch {
    return false;
  }
}

// On Linux, decide whether Chromium's sandbox has to be switched off for the app to start at all.
// `restrictFlag` is the content of /proc/sys/kernel/apparmor_restrict_unprivileged_userns (or null
// when absent) and `apparmorLabel` the content of /proc/self/attr/current (or null). When the
// restriction is on, only a process running under a named AppArmor profile that grants `userns`
// (the .deb installs one) can build the namespace sandbox; an unconfined one — an AppImage, which
// has no fixed path to attach a profile to — cannot.
function needsNoSandbox(restrictFlag, apparmorLabel) {
  if (String(restrictFlag ?? "").trim() !== "1") return false;
  const label = String(apparmorLabel ?? "").replace(/\0/g, "").trim();
  return label === "" || label === "unconfined";
}

module.exports = {
  RELEASES_URL,
  BACKEND_ENV_PASSTHROUGH,
  isSafeExternalUrl,
  appOriginFor,
  isAppUrl,
  navigationDecision,
  frameNavigationAllowed,
  permissionAllowed,
  backendEnv,
  releaseUrlFor,
  readLanEnabled,
  needsNoSandbox,
};
