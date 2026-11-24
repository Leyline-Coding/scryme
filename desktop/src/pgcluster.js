// Embedded PostgreSQL lifecycle for the desktop app: private data directories, a per-install random
// superuser password, and a loopback-only listener. Kept free of Electron imports so the password
// migration can be exercised against a real throwaway cluster under plain Node (see test/).
//
// Credentials file (<data dir>/pg-credentials.json, mode 0600):
//   { "version": 1, "password": "<current>", "pending": "<next>"?, "bootstrap": true? }
// `pending` is written *before* ALTER USER runs and cleared after, so a crash at any point leaves a
// file that still names a password the cluster accepts. Nothing is ever rotated unless the new
// password has been stored first, and a failed rotation keeps using the password that works.

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

// Clusters created by scryme <= 0.28.0 used this fixed superuser password.
const LEGACY_PASSWORD = "scryme";
const CREDENTIALS_FILE = "pg-credentials.json";
const POSIX = process.platform !== "win32";   // Windows has no POSIX modes (ACLs inherit from the profile)

// 48 hex chars (192 bits): safe verbatim in a connection URL and in an SQL literal.
function generatePassword() {
  return crypto.randomBytes(24).toString("hex");
}

// Create `dir` private to the user, and tighten it if it already exists with a wider mode.
function ensurePrivateDir(dir, log = () => {}) {
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  if (!POSIX) return;
  try {
    if ((fs.statSync(dir).mode & 0o077) !== 0) fs.chmodSync(dir, 0o700);
  } catch (err) {
    log(`[data] could not restrict ${dir} to its owner: ${err}`);
  }
}

function readCredentials(file) {
  try {
    const data = JSON.parse(fs.readFileSync(file, "utf8"));
    if (data && typeof data.password === "string" && data.password) {
      return {
        password: data.password,
        pending: typeof data.pending === "string" && data.pending ? data.pending : undefined,
        bootstrap: data.bootstrap === true,
      };
    }
  } catch {
    // missing or unreadable: treated as "no stored credentials"
  }
  return null;
}

// Atomic, owner-only write: a temp file created 0600, fsynced, then renamed over the target.
function writeCredentials(file, creds) {
  const body = { version: 1, password: creds.password };
  if (creds.pending) body.pending = creds.pending;
  if (creds.bootstrap) body.bootstrap = true;
  const tmp = `${file}.${process.pid}.tmp`;
  const fd = fs.openSync(tmp, "w", 0o600);
  try {
    fs.writeFileSync(fd, `${JSON.stringify(body)}\n`);
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  if (POSIX) fs.chmodSync(tmp, 0o600);   // openSync's mode is subject to umask only downwards; be explicit
  fs.renameSync(tmp, file);
}

function isAuthFailure(err) {
  // SQLSTATE class 28 = invalid authorization specification (28P01 wrong password, 28000 …).
  return Boolean(err && typeof err.code === "string" && err.code.startsWith("28"));
}

// Settle on the superuser password for a *running* cluster, rotating away from the legacy fixed
// password (or a first-run bootstrap password) to a random one.
//
//   file      — credentials file path
//   tryLogin  — async (password) => true | false (false = authentication refused; throws otherwise)
//   alter     — async (currentPassword, newPassword) => void, runs ALTER USER … PASSWORD
//   generate  — () => new password
//   log       — (message) => void
//
// Returns the password to use. Throws only when no known password is accepted (nothing was changed).
async function settlePassword({ file, tryLogin, alter, generate = generatePassword, log = () => {} }) {
  const stored = readCredentials(file);
  const candidates = [];
  for (const p of [stored?.password, stored?.pending, LEGACY_PASSWORD]) {
    if (p && !candidates.includes(p)) candidates.push(p);
  }

  let working = null;
  for (const candidate of candidates) {
    if (await tryLogin(candidate)) {
      working = candidate;
      break;
    }
  }
  if (working === null) {
    throw new Error(
      "the embedded database rejected every known password " +
      `(stored credentials: ${stored ? "present" : "missing"}). Nothing was changed.`,
    );
  }

  // Already on a settled random password.
  if (stored && working === stored.password && !stored.pending && !stored.bootstrap) return working;

  // A previous run changed the password and died before recording it: just record it.
  if (stored && stored.pending && working === stored.pending) {
    try {
      writeCredentials(file, { password: working });
    } catch (err) {
      log(`[db] could not finalise the credentials file (will retry next launch): ${err}`);
    }
    return working;
  }

  // Rotate: legacy fixed password, a first-run bootstrap password, or an interrupted attempt.
  const next = generate();
  try {
    // Persist the candidate first — once ALTER USER commits, the file must already know it.
    writeCredentials(file, { password: working, pending: next, bootstrap: stored?.bootstrap && working === stored.password });
  } catch (err) {
    log(`[db] could not store a new database password; keeping the current one: ${err}`);
    return working;
  }
  let alterError = null;
  try {
    await alter(working, next);
  } catch (err) {
    alterError = err;
  }
  // Ask the server which password it now accepts rather than trusting ALTER's outcome: a statement
  // can commit and still report an error (e.g. the connection dropping afterwards).
  let accepted = false;
  try {
    accepted = await tryLogin(next);
  } catch (err) {
    log(`[db] could not verify the new database password: ${err}`);
  }
  if (!accepted) {
    // The file still lists the working password first; rotation is attempted again next launch.
    log(`[db] could not change the database password; keeping the current one: ${
      alterError || "the new password was not accepted"}`);
    return working;
  }
  try {
    writeCredentials(file, { password: next });
  } catch (err) {
    log(`[db] could not finalise the credentials file (will retry next launch): ${err}`);
  }
  if (working === LEGACY_PASSWORD) log("[db] replaced the default database password with a random one");
  return next;
}

// Server flags: TCP on loopback only, and no unix socket at all. The default socket lives in
// world-readable /tmp; nothing here uses it (the backend and this process both connect over
// 127.0.0.1), and a socket inside the data dir could exceed the 107-byte sun_path limit for long
// paths, which would stop Postgres from starting.
const POSTGRES_FLAGS = ["-c", "listen_addresses=127.0.0.1", "-c", "unix_socket_directories="];

// Boot the cluster in `databaseDir` (initialising it on first run), make sure it has a private
// random password, and make sure database `dbName` exists.
//
// `EmbeddedPostgres` is the class from the embedded-postgres package (ESM, so the caller imports
// it). Returns { pg, password }.
async function startCluster({ EmbeddedPostgres, databaseDir, credentialsFile, port, user, dbName, log = () => {} }) {
  ensurePrivateDir(databaseDir, log);
  const isNew = !fs.existsSync(path.join(databaseDir, "PG_VERSION"));
  const stored = isNew ? null : readCredentials(credentialsFile);
  let initial = stored?.password || LEGACY_PASSWORD;
  if (isNew) {
    // The library hands initdb the password through a short-lived file in the system temp dir, so
    // this first password is only a bootstrap value: it is replaced right after the first start.
    // It is stored before initdb runs so an interrupted first launch can still log in next time.
    initial = generatePassword();
    writeCredentials(credentialsFile, { password: initial, bootstrap: true });
  }

  const pg = new EmbeddedPostgres({
    databaseDir,
    user,
    password: initial,
    port,
    persistent: true,
    authMethod: "scram-sha-256",    // only affects initdb (new clusters); existing pg_hba.conf is kept
    postgresFlags: POSTGRES_FLAGS,
    onLog: (m) => log(`[pg] ${String(m).trimEnd()}`),
    onError: (m) => log(`[pg] ${String((m && m.message) || m).trimEnd()}`),
  });
  if (isNew) await pg.initialise();
  await pg.start();

  // The library's client helper reads the password from its options and defaults to "localhost",
  // which may resolve to ::1 — always name the loopback address we actually listen on.
  const connect = async (password, database = "postgres") => {
    pg.options.password = password;
    const client = pg.getPgClient(database, "127.0.0.1");
    await client.connect();
    return client;
  };
  const tryLogin = async (password) => {
    let client;
    try {
      client = await connect(password);
    } catch (err) {
      if (isAuthFailure(err)) return false;
      throw err;
    }
    await client.end();
    return true;
  };
  const alter = async (current, next) => {
    const client = await connect(current);
    try {
      await client.query(
        `ALTER USER ${client.escapeIdentifier(user)} PASSWORD ${client.escapeLiteral(next)}`,
      );
    } finally {
      await client.end();
    }
  };

  let password;
  try {
    password = await settlePassword({ file: credentialsFile, tryLogin, alter, log });
  } catch (err) {
    await pg.stop().catch(() => {});
    throw err;
  }
  pg.options.password = password;

  const client = await connect(password);
  try {
    const { rowCount } = await client.query("SELECT 1 FROM pg_database WHERE datname = $1", [dbName]);
    if (!rowCount) await client.query(`CREATE DATABASE ${client.escapeIdentifier(dbName)}`);
  } finally {
    await client.end();
  }
  return { pg, password };
}

module.exports = {
  LEGACY_PASSWORD,
  CREDENTIALS_FILE,
  POSTGRES_FLAGS,
  generatePassword,
  ensurePrivateDir,
  readCredentials,
  writeCredentials,
  isAuthFailure,
  settlePassword,
  startCluster,
};
