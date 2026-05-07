/**
 * Atlas Code CLI — tool implementations.
 *
 * All tools are workspace-sandboxed: resolved paths must start with
 * workspaceRoot.  Any attempt to escape the workspace returns an error.
 */

import fs from "fs";
import path from "path";
import { execSync } from "child_process";
import type { ToolHandler, ToolResult } from "./types.js";

// Minimal error-like interface so we don't need @types/node for SysError
interface SysError extends Error {
  code?: string;
  message: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function ok(output: string): ToolResult {
  return { success: true, output };
}

function err(error: string): ToolResult {
  return { success: false, output: "", error };
}

/**
 * Resolve `userPath` relative to `workspaceRoot` and verify the result is
 * still inside the workspace (prevents path-traversal).
 */
function resolveSafe(
  userPath: string,
  workspaceRoot: string
): { resolved: string } | { error: string } {
  const resolved = path.resolve(workspaceRoot, userPath);
  const root = path.resolve(workspaceRoot);
  if (!resolved.startsWith(root + path.sep) && resolved !== root) {
    return { error: `Path is outside workspace: ${userPath}` };
  }
  return { resolved };
}

/** Format file content with 1-based line numbers: "   1 | ..." */
function numberLines(lines: string[], startLine = 1): string {
  return lines
    .map((l, i) => {
      const n = String(i + startLine).padStart(4);
      return `${n} | ${l}`;
    })
    .join("\n");
}

/** Save a backup of an existing file to .atlas/backups/ */
function backup(resolvedPath: string, workspaceRoot: string): void {
  try {
    const existing = fs.readFileSync(resolvedPath);
    const backupDir = path.join(workspaceRoot, ".atlas", "backups");
    fs.mkdirSync(backupDir, { recursive: true });
    const bname = path.basename(resolvedPath);
    const ts = Date.now();
    fs.writeFileSync(path.join(backupDir, `${bname}.${ts}.bak`), existing);
  } catch {
    // If the file doesn't exist yet there is nothing to back up — ignore.
  }
}

// ---------------------------------------------------------------------------
// Glob helpers for search_files
// ---------------------------------------------------------------------------

function matchGlob(pattern: string, filePath: string): boolean {
  const regexStr = pattern
    .replace(/[.+^${}()|[\]\\]/g, "\\$&") // escape regex specials (not * ?)
    .replace(/\*\*/g, "§§") // placeholder for **
    .replace(/\*/g, "[^/]*") // * → any chars except /
    .replace(/§§/g, ".*"); // ** → any chars including /
  try {
    return new RegExp(`^${regexStr}$`).test(filePath);
  } catch {
    return false;
  }
}

const DEFAULT_SKIP_DIRS = new Set([
  "node_modules",
  ".git",
  "dist",
  "__pycache__",
  ".next",
]);

/** Recursively walk a directory, yielding relative paths of files. */
function* walkFiles(
  dir: string,
  base: string,
  skipDirs: Set<string> = DEFAULT_SKIP_DIRS
): Generator<string> {
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    if (entry.isDirectory()) {
      if (skipDirs.has(entry.name)) continue;
      yield* walkFiles(path.join(dir, entry.name), base, skipDirs);
    } else if (entry.isFile()) {
      yield path.relative(base, path.join(dir, entry.name));
    }
  }
}

/** Check whether a buffer looks like a binary file (null byte in first 512 B). */
function isBinary(filePath: string): boolean {
  try {
    // Read first 512 bytes as a Buffer (globalThis.Buffer is available in Bun/Node without @types/node)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const NodeBuffer: any = (globalThis as unknown as Record<string, unknown>)["Buffer"];
    const buf = NodeBuffer.alloc(512);
    const fd = fs.openSync(filePath, "r");
    const bytesRead = fs.readSync(fd, buf, 0, 512, 0);
    fs.closeSync(fd);
    return (buf.slice(0, bytesRead) as Uint8Array).includes(0);
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Tool implementations
// ---------------------------------------------------------------------------

const read_file: ToolHandler = async (args, workspaceRoot) => {
  const userPath = args.path as string | undefined;
  if (!userPath) return err("read_file: 'path' argument is required");

  const r = resolveSafe(userPath, workspaceRoot);
  if ("error" in r) return err(r.error);

  let raw: string;
  try {
    raw = fs.readFileSync(r.resolved, "utf8");
  } catch (e: unknown) {
    return err(`read_file: ${(e as SysError).message}`);
  }

  const allLines = raw.split("\n");
  const totalLines = allLines.length;

  let startLine = typeof args.start_line === "number" ? args.start_line : 1;
  let endLine =
    typeof args.end_line === "number" ? args.end_line : totalLines;

  // Clamp
  startLine = Math.max(1, startLine);
  endLine = Math.min(totalLines, endLine);

  let lines = allLines.slice(startLine - 1, endLine);
  let truncated = false;

  if (lines.length > 500) {
    lines = lines.slice(0, 500);
    truncated = true;
  }

  let output = numberLines(lines, startLine);
  if (truncated) {
    output += `\n... [truncated at 500 lines — use start_line/end_line to read more]`;
  }

  return ok(output);
};

// ---------------------------------------------------------------------------

const write_file: ToolHandler = async (args, workspaceRoot) => {
  const userPath = args.path as string | undefined;
  const content = args.content as string | undefined;
  if (!userPath) return err("write_file: 'path' argument is required");
  if (content === undefined) return err("write_file: 'content' argument is required");

  const r = resolveSafe(userPath, workspaceRoot);
  if ("error" in r) return err(r.error);

  // Backup existing file before overwriting
  backup(r.resolved, workspaceRoot);

  // Ensure parent dirs exist
  fs.mkdirSync(path.dirname(r.resolved), { recursive: true });

  try {
    fs.writeFileSync(r.resolved, content, "utf8");
  } catch (e: unknown) {
    return err(`write_file: ${(e as SysError).message}`);
  }

  return ok(`Written ${new TextEncoder().encode(content).length} bytes to ${userPath}`);
};

// ---------------------------------------------------------------------------

const edit_file: ToolHandler = async (args, workspaceRoot) => {
  const userPath = args.path as string | undefined;
  const oldString = args.old_string as string | undefined;
  const newString = args.new_string as string | undefined;

  if (!userPath) return err("edit_file: 'path' argument is required");
  if (oldString === undefined) return err("edit_file: 'old_string' argument is required");
  if (newString === undefined) return err("edit_file: 'new_string' argument is required");

  const r = resolveSafe(userPath, workspaceRoot);
  if ("error" in r) return err(r.error);

  let raw: string;
  try {
    raw = fs.readFileSync(r.resolved, "utf8");
  } catch (e: unknown) {
    return err(`edit_file: ${(e as SysError).message}`);
  }

  const idx = raw.indexOf(oldString);
  if (idx === -1) {
    return err(`edit_file: old_string not found in ${userPath}`);
  }

  // Determine line number of the match (1-based)
  const lineNum = raw.slice(0, idx).split("\n").length;

  const updated = raw.slice(0, idx) + newString + raw.slice(idx + oldString.length);

  // Backup before write
  backup(r.resolved, workspaceRoot);

  try {
    fs.writeFileSync(r.resolved, updated, "utf8");
  } catch (e: unknown) {
    return err(`edit_file: ${(e as SysError).message}`);
  }

  return ok(
    `Replaced ${oldString.length} chars at line ${lineNum} in ${userPath}`
  );
};

// ---------------------------------------------------------------------------

const list_files: ToolHandler = async (args, workspaceRoot) => {
  const userPath = (args.path as string | undefined) ?? ".";
  const recursive = (args.recursive as boolean | undefined) ?? false;

  const r = resolveSafe(userPath, workspaceRoot);
  if ("error" in r) return err(r.error);

  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(r.resolved, { withFileTypes: true });
  } catch (e: unknown) {
    return err(`list_files: ${(e as SysError).message}`);
  }

  const lines: string[] = [];
  const MAX = 200;

  if (!recursive) {
    for (const entry of entries) {
      if (lines.length >= MAX) break;
      lines.push(entry.isDirectory() ? `${entry.name}/` : entry.name);
    }
    if (entries.length > MAX) {
      lines.push(`... [${entries.length - MAX} more entries not shown]`);
    }
    return ok(lines.join("\n"));
  }

  // Recursive walk with tree-like indentation
  function walkDir(dir: string, prefix: string, depth: number): void {
    if (lines.length >= MAX) return;
    let dirEntries: fs.Dirent[];
    try {
      dirEntries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of dirEntries) {
      if (lines.length >= MAX) break;
      if (DEFAULT_SKIP_DIRS.has(entry.name)) continue;
      if (entry.isDirectory()) {
        lines.push(`${prefix}${entry.name}/`);
        walkDir(path.join(dir, entry.name), prefix + "  ", depth + 1);
      } else {
        lines.push(`${prefix}${entry.name}`);
      }
    }
  }

  walkDir(r.resolved, "", 0);

  if (lines.length >= MAX) {
    lines.push(`... [truncated at ${MAX} entries]`);
  }

  return ok(lines.join("\n"));
};

// ---------------------------------------------------------------------------

const search_files: ToolHandler = async (args, workspaceRoot) => {
  const pattern = args.pattern as string | undefined;
  const userIgnore = args.ignore as string | undefined;

  if (!pattern) return err("search_files: 'pattern' argument is required");

  const extraIgnore = new Set(DEFAULT_SKIP_DIRS);
  if (userIgnore) {
    userIgnore.split(",").map((s) => s.trim()).forEach((p) => extraIgnore.add(p));
  }

  const results: string[] = [];
  const MAX = 100;

  for (const relPath of walkFiles(workspaceRoot, workspaceRoot, extraIgnore)) {
    if (results.length >= MAX) break;
    if (matchGlob(pattern, relPath)) {
      results.push(relPath);
    }
  }

  if (results.length === 0) return ok("(no matches)");

  let output = results.join("\n");
  if (results.length >= MAX) {
    output += `\n... [truncated at ${MAX} results]`;
  }
  return ok(output);
};

// ---------------------------------------------------------------------------

const grep: ToolHandler = async (args, workspaceRoot) => {
  const pattern = args.pattern as string | undefined;
  const userPath = (args.path as string | undefined) ?? ".";
  const filePattern = args.file_pattern as string | undefined;

  if (!pattern) return err("grep: 'pattern' argument is required");

  const r = resolveSafe(userPath, workspaceRoot);
  if ("error" in r) return err(r.error);

  let regex: RegExp | null;
  try {
    regex = new RegExp(pattern);
  } catch {
    regex = null; // fall back to literal search
  }

  const matches: string[] = [];
  const MAX = 50;

  for (const relPath of walkFiles(r.resolved, workspaceRoot)) {
    if (matches.length >= MAX) break;

    // Apply file_pattern filter
    if (filePattern && !matchGlob(filePattern, path.basename(relPath))) {
      continue;
    }

    const absPath = path.join(workspaceRoot, relPath);

    if (isBinary(absPath)) continue;

    let content: string;
    try {
      content = fs.readFileSync(absPath, "utf8");
    } catch {
      continue;
    }

    const lines = content.split("\n");
    for (let i = 0; i < lines.length; i++) {
      if (matches.length >= MAX) break;
      const line = lines[i];
      const hit = regex ? regex.test(line) : line.includes(pattern);
      if (hit) {
        matches.push(`${relPath}:${i + 1}: ${line}`);
      }
    }
  }

  if (matches.length === 0) return ok("(no matches)");

  const truncated = matches.length >= MAX;
  let output = matches.join("\n");
  if (truncated) output += `\n... [truncated at ${MAX} matches]`;
  output += `\n\n${matches.length}${truncated ? "+" : ""} match(es) found`;

  return ok(output);
};

// ---------------------------------------------------------------------------

const run_command: ToolHandler = async (args, workspaceRoot) => {
  const command = args.command as string | undefined;
  const timeout_ms = (args.timeout_ms as number | undefined) ?? 30000;

  if (!command) return err("run_command: 'command' argument is required");

  const start = Date.now();

  let stdout = "";
  let stderr = "";
  let exitCode = 0;

  try {
    stdout = execSync(command, {
      cwd: workspaceRoot,
      timeout: timeout_ms,
      encoding: "utf8",
      stdio: "pipe",
    });
  } catch (e: unknown) {
    const ex = e as { stdout?: string; stderr?: string; status?: number; message?: string };
    stdout = ex.stdout ?? "";
    stderr = ex.stderr ?? "";
    exitCode = ex.status ?? 1;
  }

  const duration = Date.now() - start;
  const combined = [stdout, stderr].filter(Boolean).join("\n");
  const MAX_CHARS = 10000;
  const truncated = combined.length > MAX_CHARS;
  const output =
    `Exit: ${exitCode}\nDuration: ${duration}ms\n\n` +
    (truncated
      ? combined.slice(0, MAX_CHARS) + "\n... [output truncated at 10000 chars]"
      : combined);

  return ok(output);
};

// ---------------------------------------------------------------------------

const git_status: ToolHandler = async (_args, workspaceRoot) => {
  try {
    const out = execSync("git status --short --branch", {
      cwd: workspaceRoot,
      encoding: "utf8",
      stdio: "pipe",
    });
    return ok(out.trimEnd());
  } catch (e: unknown) {
    const ex = e as { stderr?: string; message?: string };
    return err(`git_status: ${ex.stderr ?? ex.message ?? String(e)}`);
  }
};

// ---------------------------------------------------------------------------

const git_diff: ToolHandler = async (args, workspaceRoot) => {
  const staged = (args.staged as boolean | undefined) ?? false;
  const file = args.file as string | undefined;

  const parts = ["git", "diff"];
  if (staged) parts.push("--staged");
  if (file) parts.push("--", file);

  const command = parts.join(" ");

  try {
    let out = execSync(command, {
      cwd: workspaceRoot,
      encoding: "utf8",
      stdio: "pipe",
    });
    const MAX = 5000;
    if (out.length > MAX) {
      out = out.slice(0, MAX) + "\n... [diff truncated at 5000 chars]";
    }
    return ok(out || "(no diff)");
  } catch (e: unknown) {
    const ex = e as { stderr?: string; stdout?: string; message?: string };
    return err(`git_diff: ${ex.stderr ?? ex.message ?? String(e)}`);
  }
};

// ---------------------------------------------------------------------------

const git_commit_message: ToolHandler = async (_args, workspaceRoot) => {
  const runGit = (cmd: string): string => {
    try {
      return execSync(cmd, {
        cwd: workspaceRoot,
        encoding: "utf8",
        stdio: "pipe",
      }).trimEnd();
    } catch (e: unknown) {
      const ex = e as { stdout?: string };
      return (ex.stdout ?? "").trimEnd();
    }
  };

  let stat = runGit("git diff --staged --stat");
  const hasStagedChanges = stat.trim().length > 0;
  if (!hasStagedChanges) {
    stat = runGit("git diff HEAD --stat");
  }

  if (!stat.trim()) {
    return ok("(no changes detected — nothing to commit)");
  }

  // Parse changed files from stat for a conventional-commits suggestion
  const lines = stat.split("\n");
  const changedFiles = lines
    .filter((l) => l.match(/\|\s+\d+/))
    .map((l) => l.split("|")[0].trim());

  // Heuristic: guess scope from most common directory prefix
  let scope = "";
  const dirs = changedFiles
    .map((f) => f.split("/")[0])
    .filter((d) => d && !d.includes("."));
  if (dirs.length > 0) {
    const freq: Record<string, number> = {};
    dirs.forEach((d) => (freq[d] = (freq[d] ?? 0) + 1));
    scope = Object.entries(freq).sort((a, b) => b[1] - a[1])[0][0];
  }

  // Guess type from file extensions / names
  const allFiles = changedFiles.join(" ").toLowerCase();
  let type = "chore";
  if (allFiles.includes("test") || allFiles.includes("spec")) type = "test";
  else if (allFiles.includes(".md")) type = "docs";
  else if (allFiles.includes("fix") || allFiles.includes("bug")) type = "fix";
  else if (changedFiles.length > 0) type = "feat";

  const scopePart = scope ? `(${scope})` : "";
  const suggestion = `${type}${scopePart}: <describe your change here>`;

  const output =
    `--- git diff stat (${hasStagedChanges ? "staged" : "HEAD"}) ---\n` +
    stat +
    `\n\n--- suggested commit message ---\n${suggestion}`;

  return ok(output);
};

// ---------------------------------------------------------------------------

const create_directory: ToolHandler = async (args, workspaceRoot) => {
  const userPath = args.path as string | undefined;
  if (!userPath) return err("create_directory: 'path' argument is required");

  const r = resolveSafe(userPath, workspaceRoot);
  if ("error" in r) return err(r.error);

  try {
    fs.mkdirSync(r.resolved, { recursive: true });
  } catch (e: unknown) {
    return err(`create_directory: ${(e as SysError).message}`);
  }

  return ok(`Created directory: ${userPath}`);
};

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

export const toolHandlers: Record<string, ToolHandler> = {
  read_file,
  write_file,
  edit_file,
  list_files,
  search_files,
  grep,
  run_command,
  git_status,
  git_diff,
  git_commit_message,
  create_directory,
};
