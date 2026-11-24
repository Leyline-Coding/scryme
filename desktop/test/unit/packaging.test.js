// Guards the asarUnpack list in electron-builder.yml. main.js imports embedded-postgres from
// app.asar.unpacked, and a module there can only resolve dependencies that are unpacked too — so
// every package in the library's dependency closure must match an asarUnpack pattern. A dependency
// bump that adds a package would otherwise only fail at runtime, in a packaged build.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..", "..");
const NODE_MODULES = path.join(ROOT, "node_modules");

function asarUnpackPatterns() {
  const yml = fs.readFileSync(path.join(ROOT, "electron-builder.yml"), "utf8");
  const block = yml.split(/^asarUnpack:\s*$/m)[1];
  assert.ok(block, "electron-builder.yml has an asarUnpack list");
  const patterns = [];
  for (const line of block.split("\n").slice(1)) {
    const item = line.match(/^\s+-\s+"?([^"\s]+)"?\s*$/);
    if (!item) break;
    patterns.push(item[1]);
  }
  return patterns;
}

// "node_modules/pg-*/**" → matches package name "pg-pool"; "node_modules/@scope/**" → any "@scope/x".
function covers(pattern, name) {
  const m = pattern.match(/^node_modules\/(.+)\/\*\*$/);
  if (!m) return false;
  const glob = m[1];
  if (glob.startsWith("@") && !glob.includes("/")) return name.startsWith(`${glob}/`);
  const re = new RegExp(`^${glob.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, "[^/]*")}$`);
  return re.test(name);
}

function closure(start) {
  const seen = new Set();
  const queue = [start];
  while (queue.length) {
    const name = queue.shift();
    if (seen.has(name)) continue;
    const manifest = path.join(NODE_MODULES, name, "package.json");
    if (!fs.existsSync(manifest)) continue;   // optional dependency not installed on this platform
    seen.add(name);
    const pkg = JSON.parse(fs.readFileSync(manifest, "utf8"));
    queue.push(...Object.keys({ ...pkg.dependencies, ...pkg.optionalDependencies }));
  }
  return [...seen].sort();
}

test("asar is on and every embedded-postgres dependency is unpacked", { skip: !fs.existsSync(NODE_MODULES) && "run npm ci first" }, () => {
  const yml = fs.readFileSync(path.join(ROOT, "electron-builder.yml"), "utf8");
  assert.match(yml, /^asar:\s*true\s*$/m);
  const patterns = asarUnpackPatterns();
  const packages = closure("embedded-postgres");
  assert.ok(packages.includes("pg") && packages.some((p) => p.startsWith("@embedded-postgres/")),
    `unexpected closure: ${packages.join(", ")}`);
  const uncovered = packages.filter((name) => !patterns.some((p) => covers(p, name)));
  assert.deepEqual(uncovered, [], `packages missing from asarUnpack: ${uncovered.join(", ")}`);
});

test("the updater stays inside the asar", () => {
  const patterns = asarUnpackPatterns();
  for (const name of ["electron-updater", "builder-util-runtime", "js-yaml", "semver"]) {
    assert.equal(patterns.some((p) => covers(p, name)), false, name);
  }
});

test("fuses are configured", () => {
  const yml = fs.readFileSync(path.join(ROOT, "electron-builder.yml"), "utf8");
  for (const [fuse, value] of Object.entries({
    runAsNode: false,
    enableNodeOptionsEnvironmentVariable: false,
    enableNodeCliInspectArguments: false,
    onlyLoadAppFromAsar: true,
    enableEmbeddedAsarIntegrityValidation: true,
  })) {
    assert.match(yml, new RegExp(`^  ${fuse}:\\s*${value}\\s*$`, "m"), fuse);
  }
});
