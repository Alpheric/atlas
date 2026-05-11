/**
 * Atlas CLI — zero-impact auto-updater
 *
 * Spawns a completely detached child process that does the update work.
 * The main CLI process is 100% unaffected — no event loop hold, no CPU
 * spike, no delay at startup or exit.
 *
 * The child writes ~/.atlas-cli/update.log for debugging if anything
 * goes wrong. Users never see anything.
 */

import { spawn }  from "child_process";
import fs         from "fs";
import path       from "path";
import os         from "os"; // kept for INSTALL_DIR (os.homedir)

export const INSTALL_DIR  = path.join(os.homedir(), ".atlas-cli");
export const VERSION_FILE = path.join(INSTALL_DIR, "version.txt");
const BASE_URL            = "https://atlas.alpheric.ai";

/** Read locally installed version string (empty if not written yet). */
export function localVersion(): string {
  try { return fs.readFileSync(VERSION_FILE, "utf8").trim(); }
  catch { return ""; }
}

/**
 * Launch the update worker as a fully detached process and immediately
 * return. The worker has no stdio attached to the terminal — the user
 * sees and feels nothing.
 */
export function checkForUpdates(): void {
  // Inline the worker as a self-contained Bun script so we don't need
  // a separate file on disk.
  const workerScript = /* ts */ `
import fs   from "fs";
import path from "path";

const BASE_URL    = ${JSON.stringify(BASE_URL)};
const INSTALL_DIR = ${JSON.stringify(INSTALL_DIR)};
const VER_FILE    = ${JSON.stringify(VERSION_FILE)};
const LOG_FILE    = path.join(INSTALL_DIR, "update.log");

function log(msg) {
  try { fs.appendFileSync(LOG_FILE, new Date().toISOString() + "  " + msg + "\\n"); }
  catch {}
}

async function run() {
  // 1. Fetch remote version (small file, quick)
  let remote;
  try {
    const r = await fetch(BASE_URL + "/downloads/version.txt", {
      signal: AbortSignal.timeout(5_000),
    });
    if (!r.ok) return;
    remote = (await r.text()).trim();
  } catch { return; }

  if (!remote) return;

  // 2. Compare with local
  let local = "";
  try { local = fs.readFileSync(VER_FILE, "utf8").trim(); } catch {}
  if (local === remote) return;

  log("update available: " + local + " → " + remote);

  // 3. Download atlas.js and yoga.wasm directly — no tar extraction needed.
  //    macOS BSD tar --strip-components silently fails to overwrite existing
  //    files, so we bypass tar entirely and write files directly.
  const distDir = path.join(INSTALL_DIR, "dist");
  try { fs.mkdirSync(distDir, { recursive: true }); } catch {}

  const files = [
    { url: BASE_URL + "/downloads/atlas.js",  dest: path.join(distDir, "atlas.js"),  mode: 0o644 },
    { url: BASE_URL + "/downloads/yoga.wasm", dest: path.join(distDir, "yoga.wasm"), mode: 0o644 },
  ];

  for (const file of files) {
    try {
      const r = await fetch(file.url, { signal: AbortSignal.timeout(120_000) });
      if (!r.ok) { log("download failed: " + r.status + " " + file.url); return; }
      const buf = Buffer.from(await r.arrayBuffer());
      fs.writeFileSync(file.dest, buf, { mode: file.mode });
      // Force mtime update so Bun doesn't serve stale bytecode cache
      const now = new Date();
      try { fs.utimesSync(file.dest, now, now); } catch {}
      log("wrote " + file.dest + " (" + buf.length + " bytes)");
    } catch (e) { log("error writing " + file.url + ": " + e); return; }
  }

  // 4. Stamp new version
  try { fs.writeFileSync(VER_FILE, remote + "\\n"); }
  catch (e) { log("version stamp failed: " + e); return; }

  log("updated to " + remote + " ✓");
}

run().catch(() => {});
`;

  // Bulletproof spawn: any error here (EACCES, ENOENT, asynchronously
  // emitted 'error' events) MUST be swallowed silently — the auto-updater
  // is best-effort. A failure here must never crash the CLI.
  try {
    const bunBin = process.execPath; // e.g. /home/user/.bun/bin/bun

    // Sanity-check that the binary is executable for the current user.
    // If not, skip entirely — spawn would throw EACCES.
    try {
      fs.accessSync(bunBin, fs.constants.X_OK);
    } catch {
      return;
    }

    let child;
    try {
      child = spawn(bunBin, ["--eval", workerScript], {
        detached: true,
        stdio:    "ignore",
        env:      process.env,
      });
    } catch {
      return; // spawn threw synchronously (EACCES, ENOENT, etc.)
    }

    // Catch asynchronous 'error' events so they don't bubble to the
    // unhandledRejection / uncaughtException handlers and crash the app.
    try { child.on("error", () => { /* swallow */ }); } catch {}
    try { child.unref(); } catch {}
  } catch {
    // Last-ditch catch — never crash
  }
}
