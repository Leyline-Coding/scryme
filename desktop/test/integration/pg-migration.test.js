// Real-cluster check of the password migration: needs the embedded-postgres binaries (`npm ci`).
// Run with `npm run test:integration`. Everything lives in a throwaway temp directory.
//
//   1. create a cluster the way scryme <= 0.28.0 did (fixed password "scryme", default flags)
//   2. run the new startup logic twice
//   3. confirm it connects both times, the credentials file is 0600, the old password is refused,
//      the server only listens on 127.0.0.1 and has no unix socket

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");

const pgcluster = require("../../src/pgcluster");

const POSIX = process.platform !== "win32";
const USER = "scryme";
const DB = "scryme";

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

async function canLogin(pg, password) {
  const saved = pg.options.password;
  pg.options.password = password;
  const client = pg.getPgClient("postgres", "127.0.0.1");
  pg.options.password = saved;
  try {
    await client.connect();
  } catch (err) {
    if (pgcluster.isAuthFailure(err)) return false;
    throw err;
  }
  await client.end();
  return true;
}

test("legacy cluster is migrated to a random password without losing access", { timeout: 180000 }, async (t) => {
  const { default: EmbeddedPostgres } = await import("embedded-postgres");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "scryme-pgmig-"));
  const databaseDir = path.join(root, "pg");
  const credentialsFile = path.join(root, pgcluster.CREDENTIALS_FILE);
  let running = null;
  t.after(async () => {
    if (running) await running.stop().catch(() => {});
    fs.rmSync(root, { recursive: true, force: true });
  });
  const quiet = { onLog: () => {}, onError: () => {} };

  // 1. The old way: fixed password, library defaults, a table with a row to prove data survives.
  const oldPort = await freePort();
  const old = new EmbeddedPostgres({ databaseDir, user: USER, password: "scryme", port: oldPort, persistent: true, ...quiet });
  running = old;
  await old.initialise();
  await old.start();
  await old.createDatabase(DB);
  {
    const client = old.getPgClient(DB, "127.0.0.1");
    await client.connect();
    await client.query("CREATE TABLE keepsake (v text)");
    await client.query("INSERT INTO keepsake VALUES ('still here')");
    await client.end();
  }
  await old.stop();
  running = null;
  assert.equal(fs.existsSync(credentialsFile), false);

  // 2a. First launch with the new logic.
  const logs = [];
  const start = async () => {
    const port = await freePort();
    const result = await pgcluster.startCluster({
      EmbeddedPostgres, databaseDir, credentialsFile, port, user: USER, dbName: DB, log: (m) => logs.push(m),
    });
    running = result.pg;
    return { ...result, port };
  };
  const first = await start();
  assert.match(first.password, /^[0-9a-f]{48}$/);
  assert.equal(await canLogin(first.pg, first.password), true, "new password works");
  assert.equal(await canLogin(first.pg, "scryme"), false, "old password is refused");
  assert.deepEqual(JSON.parse(fs.readFileSync(credentialsFile, "utf8")), { version: 1, password: first.password });
  if (POSIX) {
    assert.equal(fs.statSync(credentialsFile).mode & 0o777, 0o600, "credentials file is 0600");
    assert.equal(fs.statSync(databaseDir).mode & 0o077, 0, "cluster directory is owner-only");
  }
  assert.ok(logs.some((m) => /replaced the default database password/.test(m)));

  // Loopback only, no unix socket.
  {
    const client = first.pg.getPgClient(DB, "127.0.0.1");
    await client.connect();
    const listen = await client.query("SHOW listen_addresses");
    const sockets = await client.query("SHOW unix_socket_directories");
    const row = await client.query("SELECT v FROM keepsake");
    await client.end();
    assert.equal(listen.rows[0].listen_addresses, "127.0.0.1");
    assert.equal(sockets.rows[0].unix_socket_directories, "");
    assert.equal(row.rows[0].v, "still here", "existing data is intact");
  }
  if (POSIX) {
    assert.equal(fs.existsSync(`/tmp/.s.PGSQL.${first.port}`), false, "no socket in /tmp");
    assert.equal(fs.existsSync(path.join(os.tmpdir(), `.s.PGSQL.${first.port}`)), false);
  }
  await first.pg.stop();
  running = null;

  // 2b. Second launch: same password, still connects, nothing rotated again.
  const second = await start();
  assert.equal(second.password, first.password);
  assert.equal(await canLogin(second.pg, second.password), true);
  assert.equal(await canLogin(second.pg, "scryme"), false);
  await second.pg.stop();
  running = null;
});

test("a brand-new cluster never has the default password", { timeout: 180000 }, async (t) => {
  const { default: EmbeddedPostgres } = await import("embedded-postgres");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "scryme-pgnew-"));
  const databaseDir = path.join(root, "pg");
  const credentialsFile = path.join(root, pgcluster.CREDENTIALS_FILE);
  let running = null;
  t.after(async () => {
    if (running) await running.stop().catch(() => {});
    fs.rmSync(root, { recursive: true, force: true });
  });

  const start = async () => {
    const result = await pgcluster.startCluster({
      EmbeddedPostgres, databaseDir, credentialsFile, port: await freePort(), user: USER, dbName: DB,
    });
    running = result.pg;
    return result;
  };
  const first = await start();
  assert.equal(await canLogin(first.pg, first.password), true);
  assert.equal(await canLogin(first.pg, "scryme"), false);
  assert.deepEqual(JSON.parse(fs.readFileSync(credentialsFile, "utf8")), { version: 1, password: first.password });
  if (POSIX) assert.equal(fs.statSync(credentialsFile).mode & 0o777, 0o600);
  {
    const client = first.pg.getPgClient(DB, "127.0.0.1");
    await client.connect();   // the application database was created
    await client.end();
  }
  await first.pg.stop();
  running = null;

  const second = await start();
  assert.equal(second.password, first.password);
  await second.pg.stop();
  running = null;
});
