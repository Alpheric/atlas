/**
 * Session persistence — saves and restores conversation history.
 *
 * Sessions are stored as JSON files in ~/.atlas/sessions/.
 * Each session is identified by a UUID and carries workspace path,
 * model, full message history, and a short text preview.
 *
 * Usage:
 *   saveSession(session)           Write / overwrite a session file
 *   listSessions(limit?)           Newest-first list of SavedSession metadata
 *   loadSession(id)                Load a specific session by partial ID
 *   deleteOldSessions(keep?)       Prune sessions older than `keep` days
 */

import fs   from "fs";
import path from "path";
import os   from "os";
import type { Message } from "./api.js";

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

const SESSIONS_DIR = path.join(os.homedir(), ".atlas", "sessions");

function ensureDir() {
  if (!fs.existsSync(SESSIONS_DIR)) {
    fs.mkdirSync(SESSIONS_DIR, { recursive: true });
  }
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SavedSession {
  id: string;
  timestamp: string;   // ISO-8601
  workspace: string;   // absolute cwd
  model: string;
  messages: Message[];
  preview: string;     // first user message, up to 100 chars
  turnCount: number;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/** Write (overwrite) a session to disk. */
export function saveSession(session: SavedSession): void {
  try {
    ensureDir();
    // Filename: <ISO-date>_<id>.json — sorts chronologically
    const fname = `${session.timestamp.replace(/[:.]/g, "-")}_${session.id}.json`;
    fs.writeFileSync(
      path.join(SESSIONS_DIR, fname),
      JSON.stringify(session, null, 2),
      "utf-8"
    );
  } catch { /* never surface — session save must not disrupt UX */ }
}

/** Return up to `limit` sessions, newest first. */
export function listSessions(limit = 30): SavedSession[] {
  try {
    ensureDir();
    return fs
      .readdirSync(SESSIONS_DIR)
      .filter(f => f.endsWith(".json"))
      .sort()
      .reverse()
      .slice(0, limit)
      .map(f => {
        try {
          return JSON.parse(
            fs.readFileSync(path.join(SESSIONS_DIR, f), "utf-8")
          ) as SavedSession;
        } catch { return null; }
      })
      .filter(Boolean) as SavedSession[];
  } catch { return []; }
}

/** Load a session by its ID (or any unique ID prefix). */
export function loadSession(id: string): SavedSession | null {
  try {
    ensureDir();
    const match = fs
      .readdirSync(SESSIONS_DIR)
      .filter(f => f.includes(id) && f.endsWith(".json"))[0];
    if (!match) return null;
    return JSON.parse(fs.readFileSync(path.join(SESSIONS_DIR, match), "utf-8"));
  } catch { return null; }
}

/** Delete sessions older than `keepDays` days (default 30). */
export function deleteOldSessions(keepDays = 30): void {
  try {
    ensureDir();
    const cutoff = Date.now() - keepDays * 24 * 60 * 60 * 1000;
    for (const f of fs.readdirSync(SESSIONS_DIR)) {
      if (!f.endsWith(".json")) continue;
      try {
        const p = path.join(SESSIONS_DIR, f);
        if (fs.statSync(p).mtimeMs < cutoff) fs.unlinkSync(p);
      } catch { /* skip */ }
    }
  } catch { /* silent */ }
}

/** Generate a short human-readable session ID (not UUID). */
export function newSessionId(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}
