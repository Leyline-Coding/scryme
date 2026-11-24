const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const policy = require("../../src/security");

const ORIGIN = policy.appOriginFor(8765);

test("only https URLs are opened externally", () => {
  for (const ok of ["https://scryfall.com/docs/syntax", "https://github.com/Leyline-Coding/scryme/releases/latest"]) {
    assert.equal(policy.isSafeExternalUrl(ok), true, ok);
  }
  for (const bad of [
    "http://example.com/", "file:///etc/passwd", "smb://host/share", "ms-msdt:/id", "mailto:a@b.c",
    "javascript:alert(1)", "data:text/html,x", "search-ms:query=x", "vscode://x", "about:blank",
    "https://user:pw@example.com/", "https://", "not a url", "", null, undefined, "//example.com",
    "\\\\host\\share", "C:\\Windows\\system32\\calc.exe",
  ]) {
    assert.equal(policy.isSafeExternalUrl(bad), false, String(bad));
  }
});

test("the app origin is exactly http://127.0.0.1:<port>", () => {
  assert.equal(ORIGIN, "http://127.0.0.1:8765");
  assert.equal(policy.isAppUrl("http://127.0.0.1:8765/", ORIGIN), true);
  assert.equal(policy.isAppUrl("http://127.0.0.1:8765/card/abc?x=1#y", ORIGIN), true);
  for (const bad of [
    "http://127.0.0.1:8766/", "http://localhost:8765/", "https://127.0.0.1:8765/",
    "http://127.0.0.1.evil.example:8765/", "http://127.0.0.1:8765@evil.example/",
    "http://evil.example/http://127.0.0.1:8765/", "http://[::1]:8765/", "file:///x", "about:blank", "",
  ]) {
    assert.equal(policy.isAppUrl(bad, ORIGIN), false, bad);
  }
  assert.equal(policy.isAppUrl("http://127.0.0.1:8765/", null), false, "no origin known yet → nothing is the app");
});

test("navigation decisions: own origin stays, https goes to the browser, the rest is dropped", () => {
  assert.equal(policy.navigationDecision("http://127.0.0.1:8765/decks", ORIGIN), "allow");
  assert.equal(policy.navigationDecision("https://scryfall.com/card/x", ORIGIN), "external");
  assert.equal(policy.navigationDecision("http://example.com/", ORIGIN), "deny");
  assert.equal(policy.navigationDecision("file:///etc/passwd", ORIGIN), "deny");
  assert.equal(policy.navigationDecision("smb://host/share", ORIGIN), "deny");
  assert.equal(policy.navigationDecision("data:text/html,<script>1</script>", ORIGIN), "deny");
  assert.equal(policy.navigationDecision("http://127.0.0.1:8765/", null), "deny");
});

test("sub-frames may only show the app or an empty document", () => {
  assert.equal(policy.frameNavigationAllowed("http://127.0.0.1:8765/x", ORIGIN), true);
  assert.equal(policy.frameNavigationAllowed("about:blank", ORIGIN), true);
  assert.equal(policy.frameNavigationAllowed("about:srcdoc", ORIGIN), true);
  assert.equal(policy.frameNavigationAllowed("https://example.com/", ORIGIN), false);
  assert.equal(policy.frameNavigationAllowed("file:///x", ORIGIN), false);
});

test("permissions are denied by default and granted only to the app origin", () => {
  assert.equal(policy.permissionAllowed("clipboard-sanitized-write", `${ORIGIN}/settings`, ORIGIN), true);
  assert.equal(policy.permissionAllowed("notifications", ORIGIN, ORIGIN), true);
  for (const p of ["media", "geolocation", "clipboard-read", "openExternal", "fullscreen", "pointerLock",
    "midi", "hid", "serial", "usb", "display-capture", "fileSystem", "unknown"]) {
    assert.equal(policy.permissionAllowed(p, ORIGIN, ORIGIN), false, p);
  }
  assert.equal(policy.permissionAllowed("notifications", "https://example.com", ORIGIN), false);
  assert.equal(policy.permissionAllowed("clipboard-sanitized-write", "", ORIGIN), false);
  assert.equal(policy.permissionAllowed("notifications", ORIGIN, null), false);
});

test("backend environment drops inherited SCRYME_* settings except the allow-list", () => {
  const env = policy.backendEnv({
    PATH: "/usr/bin",
    HOME: "/home/u",
    HTTPS_PROXY: "http://proxy:3128",
    SCRYME_LAN_GUARD: "0",
    scryme_lan_guard: "0",
    Scryme_Read_Only: "true",
    SCRYME_DATABASE_URL: "postgresql://elsewhere/db",
    SCRYME_DB_PASSWORD: "x",
    SCRYME_DATA_DIR: "/somewhere/else",
    SCRYME_PORT: "1",
    SCRYME_SCRYFALL_API_BASE: "https://evil.example",
    SCRYME_DESKTOP_DATA_DIR: "/d",
    SCRYME_PYTHON: "/venv/bin/python",
    SCRYME_LLM_BASE_URL: "http://localhost:11434/v1",
    scryme_backup_dir: "/backups",
    UNDEFINED_ONE: undefined,
  }, { SCRYME_LAN_GUARD: "1", SCRYME_PORT: "8765", SCRYME_DATA_DIR: "/data/files" });

  assert.deepEqual(env, {
    PATH: "/usr/bin",
    HOME: "/home/u",
    HTTPS_PROXY: "http://proxy:3128",
    SCRYME_LLM_BASE_URL: "http://localhost:11434/v1",
    SCRYME_BACKUP_DIR: "/backups",
    SCRYME_LAN_GUARD: "1",
    SCRYME_PORT: "8765",
    SCRYME_DATA_DIR: "/data/files",
  });
  const guards = Object.keys(env).filter((k) => k.toUpperCase() === "SCRYME_LAN_GUARD");
  assert.deepEqual(guards, ["SCRYME_LAN_GUARD"]);
});

test("release URLs are built only from plain version strings", () => {
  assert.equal(policy.releaseUrlFor("0.29.0"), "https://github.com/Leyline-Coding/scryme/releases/tag/v0.29.0");
  assert.equal(policy.releaseUrlFor("1.0.0-beta.2"), "https://github.com/Leyline-Coding/scryme/releases/tag/v1.0.0-beta.2");
  for (const odd of ["", null, undefined, "1.0", "1.0.0/../../evil", "1.0.0?x=y", "1.0.0#frag", "v1.0.0", "1.0.0 "]) {
    assert.equal(policy.releaseUrlFor(odd), "https://github.com/Leyline-Coding/scryme/releases/latest", String(odd));
  }
  assert.equal(policy.isSafeExternalUrl(policy.releaseUrlFor("0.29.0")), true);
  assert.equal(policy.isSafeExternalUrl(policy.releaseUrlFor(null)), true);
});

test("LAN state file: anything unreadable means off", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "scryme-lan-"));
  try {
    const file = path.join(dir, "lan.json");
    assert.equal(policy.readLanEnabled(file), false, "missing");
    fs.writeFileSync(file, JSON.stringify({ enabled: true, code: "abc" }));
    assert.equal(policy.readLanEnabled(file), true);
    fs.writeFileSync(file, JSON.stringify({ enabled: false, code: "" }));
    assert.equal(policy.readLanEnabled(file), false);
    fs.writeFileSync(file, "{truncated");
    assert.equal(policy.readLanEnabled(file), false, "corrupt");
    fs.writeFileSync(file, "null");
    assert.equal(policy.readLanEnabled(file), false, "null");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("the sandbox is only dropped when userns is restricted and no AppArmor profile is attached", () => {
  assert.equal(policy.needsNoSandbox(null, null), false, "no AppArmor restriction file");
  assert.equal(policy.needsNoSandbox("0\n", "unconfined\n"), false, "restriction off");
  assert.equal(policy.needsNoSandbox("1\n", "unconfined\n"), true, "AppImage on Ubuntu 24.04");
  assert.equal(policy.needsNoSandbox("1\n", null), true, "label unreadable → assume unconfined");
  assert.equal(policy.needsNoSandbox("1\n", "scryme (unconfined)\n"), false, ".deb profile attached");
  assert.equal(policy.needsNoSandbox("1\n", "scryme (unconfined)\0"), false);
});
