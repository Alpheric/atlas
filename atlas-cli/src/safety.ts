import path from "path";

export interface SafetyCheck {
  safe: boolean;
  reason?: string;
  requiresExtraConfirm?: boolean;
}

// Dangerous shell command patterns
export const DANGEROUS_COMMANDS: string[] = [
  "rm -rf",
  "sudo",
  "chmod -R 777",
  "curl | bash",
  "curl|bash",
  "wget | bash",
  "wget|bash",
  "dd if=",
  "mkfs",
  "shutdown",
  "reboot",
  "systemctl",
  "docker system prune",
  "git reset --hard",
  "> /dev/",
  ":(){ :|:& };:",
  "chmod 777",
];

// Secret file patterns — never read automatically
export const SECRET_FILE_PATTERNS: RegExp[] = [
  /\.env$/,
  /\.env\./,
  /\.pem$/,
  /\.key$/,
  /id_rsa/,
  /id_ed25519/,
  /service.account/,
  /credentials\.json$/,
  /secrets?\.(json|yaml|yml|toml)$/,
  /\.pfx$/,
  /\.p12$/,
];

// Patterns that need double confirmation (extra dangerous)
const EXTRA_CONFIRM_PATTERNS: string[] = [
  "rm -rf",
  "dd if=",
  "mkfs",
  "shutdown",
  "reboot",
  ":(){ :|:& };:",
  "> /dev/",
];

export function checkCommand(cmd: string): SafetyCheck {
  const cmdLower = cmd.toLowerCase();

  for (const pattern of DANGEROUS_COMMANDS) {
    if (cmdLower.includes(pattern.toLowerCase())) {
      const requiresExtraConfirm = EXTRA_CONFIRM_PATTERNS.some((p) =>
        cmdLower.includes(p.toLowerCase())
      );
      return {
        safe: false,
        reason: `Command contains dangerous pattern: "${pattern}"`,
        requiresExtraConfirm,
      };
    }
  }

  return { safe: true };
}

export function isSecretFile(filePath: string): boolean {
  const normalized = filePath.replace(/\\/g, "/");
  const basename = path.basename(normalized);
  return SECRET_FILE_PATTERNS.some(
    (pattern) => pattern.test(normalized) || pattern.test(basename)
  );
}

const SYSTEM_PATHS = ["/etc/", "/usr/", "/bin/", "/sbin/", "/lib/", "/boot/", "/sys/", "/proc/"];

export function checkFileWrite(filePath: string, workspaceRoot: string): SafetyCheck {
  // Resolve to absolute paths
  const absFilePath = path.isAbsolute(filePath)
    ? filePath
    : path.resolve(workspaceRoot, filePath);

  const absWorkspaceRoot = path.resolve(workspaceRoot);

  // Check if path escapes workspace root
  const relative = path.relative(absWorkspaceRoot, absFilePath);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    return {
      safe: false,
      reason: `File path "${filePath}" escapes workspace root "${workspaceRoot}"`,
    };
  }

  // Check system paths
  const normalizedAbs = absFilePath.replace(/\\/g, "/");
  for (const sysPath of SYSTEM_PATHS) {
    if (normalizedAbs.startsWith(sysPath)) {
      return {
        safe: false,
        reason: `File path "${filePath}" targets a system directory (${sysPath})`,
        requiresExtraConfirm: true,
      };
    }
  }

  return { safe: true };
}

const READ_ONLY_COMMANDS = [
  /^ls(\s|$)/,
  /^cat(\s|$)/,
  /^echo(\s|$)/,
  /^git\s+(status|log|diff|show|branch|remote|tag)(\s|$)/,
  /^find(\s|$)/,
  /^grep(\s|$)/,
  /^which(\s|$)/,
  /^pwd(\s|$)/,
  /^env(\s|$)/,
  /^printenv(\s|$)/,
  /^type(\s|$)/,
  /^head(\s|$)/,
  /^tail(\s|$)/,
  /^wc(\s|$)/,
  /^stat(\s|$)/,
  /^file(\s|$)/,
];

const MODERATE_COMMANDS = [
  /^npm\s+(install|i|ci|run|test|build)(\s|$)/,
  /^yarn\s+(install|add|run|test|build)(\s|$)/,
  /^pnpm\s+(install|add|run|test|build)(\s|$)/,
  /^bun\s+(install|add|run|test|build)(\s|$)/,
  /^git\s+(add|commit|stash|checkout|switch|restore)(\s|$)/,
  /^touch(\s|$)/,
  /^mkdir(\s|$)/,
  /^cp(\s|$)/,
  /^mv(\s|$)/,
  /^tee(\s|$)/,
];

export function classifyCommandRisk(cmd: string): "safe" | "moderate" | "dangerous" {
  const trimmed = cmd.trim();

  // Check dangerous first
  const dangerCheck = checkCommand(trimmed);
  if (!dangerCheck.safe) return "dangerous";

  // Check read-only patterns
  if (READ_ONLY_COMMANDS.some((re) => re.test(trimmed))) return "safe";

  // Check moderate patterns
  if (MODERATE_COMMANDS.some((re) => re.test(trimmed))) return "moderate";

  // Default: unknown commands are moderate (need review but not obviously dangerous)
  return "moderate";
}
