// Password settling logic against a fake server (the real-cluster run lives in test/integration/).

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const pgcluster = require("../../src/pgcluster");

const POSIX = process.platform !== "win32";

function scratch(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "scryme-pgauth-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return path.join(dir, pgcluster.CREDENTIALS_FILE);
}

// A stand-in for the cluster: one current password, optional injected failures.
function fakeServer(password, { alterFails = false, alterThrowsAfterCommit = false } = {}) {
  const server = {
    password,
    alters: 0,
    tryLogin: async (p) => p === server.password,
    alter: async (current, next) => {
      assert.equal(current, server.password, "ALTER must be issued with the working password");
      server.alters += 1;
      if (alterFails) throw new Error("permission denied");
      server.password = next;
      if (alterThrowsAfterCommit) throw new Error("connection lost after commit");
    },
  };
  return server;
}

const settle = (file, server, extra = {}) =>
  pgcluster.settlePassword({ file, tryLogin: server.tryLogin, alter: server.alter, ...extra });

test("legacy cluster: rotates to a random password and records it 0600", async (t) => {
  const file = scratch(t);
  const server = fakeServer(pgcluster.LEGACY_PASSWORD);

  const password = await settle(file, server);

  assert.notEqual(password, pgcluster.LEGACY_PASSWORD);
  assert.match(password, /^[0-9a-f]{48}$/);
  assert.equal(server.password, password);
  assert.deepEqual(JSON.parse(fs.readFileSync(file, "utf8")), { version: 1, password });
  if (POSIX) assert.equal(fs.statSync(file).mode & 0o777, 0o600);

  // Second launch: same password, no further ALTER.
  assert.equal(await settle(file, server), password);
  assert.equal(server.alters, 1);
});

test("ALTER fails: keeps using the old password and can still log in next time", async (t) => {
  const file = scratch(t);
  const server = fakeServer(pgcluster.LEGACY_PASSWORD, { alterFails: true });
  const logs = [];

  assert.equal(await settle(file, server, { log: (m) => logs.push(m) }), pgcluster.LEGACY_PASSWORD);
  assert.equal(server.password, pgcluster.LEGACY_PASSWORD);
  assert.ok(logs.some((m) => /keeping the current one/.test(m)));
  // Still not locked out on the following launch (and it tries again).
  assert.equal(await settle(file, server), pgcluster.LEGACY_PASSWORD);
  assert.equal(server.alters, 2);
});

test("the new password is on disk before ALTER runs", async (t) => {
  const file = scratch(t);
  const server = fakeServer(pgcluster.LEGACY_PASSWORD);
  let seenAtAlter;
  const alter = async (current, next) => {
    seenAtAlter = pgcluster.readCredentials(file);
    await server.alter(current, next);
  };

  const password = await pgcluster.settlePassword({ file, tryLogin: server.tryLogin, alter });

  assert.deepEqual(seenAtAlter, { password: pgcluster.LEGACY_PASSWORD, pending: password, bootstrap: false });
});

test("crash between ALTER and the final write: the pending password is adopted", async (t) => {
  const file = scratch(t);
  pgcluster.writeCredentials(file, { password: pgcluster.LEGACY_PASSWORD, pending: "new-pass" });
  const server = fakeServer("new-pass");   // ALTER committed, process died before recording it

  assert.equal(await settle(file, server), "new-pass");
  assert.equal(server.alters, 0);
  assert.deepEqual(pgcluster.readCredentials(file), { password: "new-pass", pending: undefined, bootstrap: false });
});

test("crash before ALTER: the stale pending password is replaced, not trusted", async (t) => {
  const file = scratch(t);
  pgcluster.writeCredentials(file, { password: pgcluster.LEGACY_PASSWORD, pending: "never-applied" });
  const server = fakeServer(pgcluster.LEGACY_PASSWORD);

  const password = await settle(file, server);

  assert.notEqual(password, "never-applied");
  assert.equal(server.password, password);
  assert.deepEqual(pgcluster.readCredentials(file), { password, pending: undefined, bootstrap: false });
});

test("ALTER errors after committing: the new password is detected and used", async (t) => {
  const file = scratch(t);
  const server = fakeServer(pgcluster.LEGACY_PASSWORD, { alterThrowsAfterCommit: true });

  // The server is asked which password it accepts, so the reported error does not mislead.
  const password = await settle(file, server);
  assert.notEqual(password, pgcluster.LEGACY_PASSWORD);
  assert.equal(server.password, password);
  assert.deepEqual(pgcluster.readCredentials(file), { password, pending: undefined, bootstrap: false });
});

test("first-run bootstrap password is replaced once", async (t) => {
  const file = scratch(t);
  pgcluster.writeCredentials(file, { password: "bootstrap-pw", bootstrap: true });
  const server = fakeServer("bootstrap-pw");

  const password = await settle(file, server);

  assert.notEqual(password, "bootstrap-pw");
  assert.deepEqual(pgcluster.readCredentials(file), { password, pending: undefined, bootstrap: false });
  assert.equal(await settle(file, server), password);
  assert.equal(server.alters, 1);
});

test("credentials file cannot be written: nothing is rotated", async (t) => {
  const file = path.join(path.dirname(scratch(t)), "missing-dir", "creds.json");
  const server = fakeServer(pgcluster.LEGACY_PASSWORD);

  assert.equal(await settle(file, server), pgcluster.LEGACY_PASSWORD);
  assert.equal(server.alters, 0);
});

test("no known password works: throws and changes nothing", async (t) => {
  const file = scratch(t);
  pgcluster.writeCredentials(file, { password: "a" });
  const server = fakeServer("something-else");

  await assert.rejects(settle(file, server), /rejected every known password/);
  assert.equal(server.alters, 0);
  assert.deepEqual(pgcluster.readCredentials(file), { password: "a", pending: undefined, bootstrap: false });
});

test("a connection error (not an auth refusal) propagates instead of trying to rotate", async (t) => {
  const file = scratch(t);
  const boom = Object.assign(new Error("connect ECONNREFUSED"), { code: "ECONNREFUSED" });
  await assert.rejects(
    pgcluster.settlePassword({ file, tryLogin: async () => { throw boom; }, alter: async () => assert.fail("no ALTER") }),
    /ECONNREFUSED/,
  );
  assert.equal(fs.existsSync(file), false);
});

test("auth failures are recognised by SQLSTATE class 28", () => {
  assert.equal(pgcluster.isAuthFailure({ code: "28P01" }), true);
  assert.equal(pgcluster.isAuthFailure({ code: "28000" }), true);
  assert.equal(pgcluster.isAuthFailure({ code: "ECONNREFUSED" }), false);
  assert.equal(pgcluster.isAuthFailure(new Error("x")), false);
  assert.equal(pgcluster.isAuthFailure(undefined), false);
});

test("private directories are created 0700 and existing ones tightened", { skip: !POSIX }, (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "scryme-dirs-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const fresh = path.join(root, "a", "b");
  const loose = path.join(root, "loose");
  fs.mkdirSync(loose, { mode: 0o755 });
  fs.chmodSync(loose, 0o755);

  pgcluster.ensurePrivateDir(fresh);
  pgcluster.ensurePrivateDir(loose);

  assert.equal(fs.statSync(fresh).mode & 0o777, 0o700);
  assert.equal(fs.statSync(loose).mode & 0o777, 0o700);
});

test("the server is told to listen on loopback only, with no unix socket", () => {
  assert.deepEqual(pgcluster.POSTGRES_FLAGS,
    ["-c", "listen_addresses=127.0.0.1", "-c", "unix_socket_directories="]);
});
