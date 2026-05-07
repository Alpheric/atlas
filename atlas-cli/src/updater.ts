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
import os         from "os";

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
import os   from "os";

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

  // 3. Download tarball
  let buf;
  try {
    const r = await fetch(BASE_URL + "/downloads/atlas-cli.tar.gz", {
      signal: AbortSignal.timeout(120_000),
    });
    if (!r.ok) { log("download failed: " + r.status); return; }
    buf = Buffer.from(await r.arrayBuffer());
  } catch (e) { log("download error: " + e); return; }

  // 4. Write to temp file
  const tmp = path.join(os.tmpdir(), "atlas-update-" + remote + ".tar.gz");
  try { fs.writeFileSync(tmp, buf); }
  catch (e) { log("write tmp failed: " + e); return; }

  // 5. Extract (overwrites dist/)
  const { spawnSync } = await import("child_process");
  const r = spawnSync("tar", ["-xzf", tmp, "-C", INSTALL_DIR, "--strip-components=1"], {
    timeout: 60_000,
  });
  try { fs.unlinkSync(tmp); } catch {}

  if (r.status !== 0) {
    log("tar failed: " + (r.stderr?.toString() ?? ""));
    return;
  }

  // 6. Stamp new version
  try { fs.writeFileSync(VER_FILE, remote + "\\n"); }
  catch (e) { log("version stamp failed: " + e); return; }

  log("updated to " + remote + " ✓");
}

run().catch(() => {});
`;

  try {
    // Find bun binary — same one running us right now
    const bunBin = process.execPath; // e.g. /home/user/.bun/bin/bun

    const child = spawn(bunBin, ["--eval", workerScript], {
      detached: true,          // fully independent OS process
      stdio:    "ignore",      // no stdin/stdout/stderr — completely silent
      env:      process.env,
    });

    child.unref(); // let the main process exit without waiting for child
  } catch {
    // If spawn fails for any reason, just skip — never crash the CLI
  }
}
