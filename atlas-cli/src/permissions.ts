import fs from "fs";
import path from "path";

export type PermissionMode = "readonly" | "ask" | "auto" | "danger";

export interface PermissionConfig {
  mode: PermissionMode;
  allow: string[]; // tool names always allowed
  ask: string[]; // tool names that always ask
  deny: string[]; // tool names/patterns always denied
}

export const DEFAULT_PERMISSIONS: PermissionConfig = {
  // "auto" mode: file reads + writes are approved automatically.
  // Only shell commands (run_command) require user confirmation.
  // Users can switch to "ask" mode with /mode ask if they want explicit approval.
  mode: "auto",
  allow: [
    "read_file",
    "list_files",
    "search_files",
    "grep",
    "git_status",
    "git_diff",
    "git_commit_message",
    "write_file",
    "edit_file",
    "create_directory",
  ],
  ask: ["run_command"],
  deny: ["delete_file"],
};

export function loadPermissions(atlasDir?: string): PermissionConfig {
  if (!atlasDir) return { ...DEFAULT_PERMISSIONS };

  const settingsPath = path.join(atlasDir, "settings.json");
  try {
    if (fs.existsSync(settingsPath)) {
      const raw = fs.readFileSync(settingsPath, "utf-8");
      const parsed = JSON.parse(raw) as Partial<PermissionConfig>;
      return {
        mode: parsed.mode ?? DEFAULT_PERMISSIONS.mode,
        allow: parsed.allow ?? [...DEFAULT_PERMISSIONS.allow],
        ask: parsed.ask ?? [...DEFAULT_PERMISSIONS.ask],
        deny: parsed.deny ?? [...DEFAULT_PERMISSIONS.deny],
      };
    }
  } catch {
    // ignore parse errors; fall back to defaults
  }
  return { ...DEFAULT_PERMISSIONS, allow: [...DEFAULT_PERMISSIONS.allow], ask: [...DEFAULT_PERMISSIONS.ask], deny: [...DEFAULT_PERMISSIONS.deny] };
}

export function savePermissions(atlasDir: string, config: PermissionConfig): void {
  fs.mkdirSync(atlasDir, { recursive: true });
  const settingsPath = path.join(atlasDir, "settings.json");
  fs.writeFileSync(settingsPath, JSON.stringify(config, null, 2), "utf-8");
}

export function checkPermission(
  tool: string,
  config: PermissionConfig
): "allow" | "ask" | "deny" {
  // Priority: deny > allow > ask > mode default
  if (config.deny.includes(tool)) return "deny";
  if (config.allow.includes(tool)) return "allow";
  if (config.ask.includes(tool)) return "ask";

  // Fall back to mode default
  switch (config.mode) {
    case "readonly": {
      // Allow read-oriented tools; deny write tools
      const readTools = new Set([
        "read_file",
        "list_files",
        "search_files",
        "grep",
        "git_status",
        "git_diff",
        "git_commit_message",
      ]);
      return readTools.has(tool) ? "allow" : "deny";
    }
    case "ask":
      return "ask";
    case "auto": {
      // Allow safe reads and edits, ask for run_command and risky ops
      const riskyTools = new Set(["run_command", "delete_file"]);
      if (riskyTools.has(tool)) return "ask";
      return "allow";
    }
    case "danger":
      return "allow";
    default:
      return "ask";
  }
}
