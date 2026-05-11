/**
 * Reads ATLAS.md and .atlas/memory.md and builds the project context
 * that is injected into every system prompt.
 */

import fs from "fs";
import path from "path";
import { WorkspaceInfo } from "./workspace.js";

export interface ProjectContext {
  atlasMd?: string;
  memoryMd?: string;
  summary: string; // compact summary for system prompt injection
}

/** Load ATLAS.md and .atlas/memory.md if they exist. */
export function loadProjectContext(info: WorkspaceInfo): ProjectContext {
  let atlasMd: string | undefined;
  let memoryMd: string | undefined;

  if (info.hasAtlasMd) {
    try {
      atlasMd = fs.readFileSync(path.join(info.cwd, "ATLAS.md"), "utf-8");
    } catch {
      // ignore
    }
  }

  if (info.hasMemoryMd) {
    try {
      memoryMd = fs.readFileSync(path.join(info.cwd, ".atlas", "memory.md"), "utf-8");
    } catch {
      // ignore
    }
  }

  const summary = buildSummary(info, atlasMd, memoryMd);
  return { atlasMd, memoryMd, summary };
}

/** Write (overwrite) .atlas/memory.md with new content. Creates the file if absent. */
export function saveMemory(atlasDir: string, content: string): void {
  const memoryPath = path.join(atlasDir, "memory.md");
  try {
    fs.writeFileSync(memoryPath, content, "utf-8");
  } catch {
    // silently ignore — e.g. read-only filesystem
  }
}

/** Load custom slash commands from .atlas/commands/*.md */
export function loadCustomCommands(atlasDir?: string): Record<string, string> {
  if (!atlasDir) return {};
  const commandsDir = path.join(atlasDir, "commands");
  if (!fs.existsSync(commandsDir)) return {};

  const commands: Record<string, string> = {};
  try {
    const files = fs.readdirSync(commandsDir);
    for (const file of files) {
      if (!file.endsWith(".md")) continue;
      const name = file.slice(0, -3); // strip .md
      const content = fs.readFileSync(path.join(commandsDir, file), "utf-8");
      commands[name] = content;
    }
  } catch {
    // ignore
  }
  return commands;
}

function buildSummary(
  info: WorkspaceInfo,
  atlasMd?: string,
  memoryMd?: string
): string {
  const parts: string[] = [];

  // Workspace basics
  parts.push(`## Workspace`);
  parts.push(`- Directory: ${info.cwd}`);
  if (info.isGit) parts.push(`- Git repository: yes (root: ${info.gitRoot ?? info.cwd})`);
  if (info.framework) parts.push(`- Framework: ${info.framework}`);
  if (info.packageManager) parts.push(`- Package manager: ${info.packageManager}`);

  if (info.detectedFiles.length > 0) {
    parts.push(`- Key files: ${info.detectedFiles.join(", ")}`);
  }

  // ATLAS.md content (truncated to 2000 chars)
  if (atlasMd) {
    parts.push(`\n## Project Context (ATLAS.md)`);
    const truncated = atlasMd.length > 2000 ? atlasMd.slice(0, 2000) + "\n... [truncated]" : atlasMd;
    parts.push(truncated);
  }

  // Memory content (truncated to 1000 chars)
  if (memoryMd) {
    parts.push(`\n## Session Memory (.atlas/memory.md)`);
    const truncated = memoryMd.length > 1000 ? memoryMd.slice(0, 1000) + "\n... [truncated]" : memoryMd;
    parts.push(truncated);
  }

  return parts.join("\n");
}
