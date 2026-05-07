import fs from "fs";
import path from "path";

export interface AuditEntry {
  timestamp: string; // ISO string
  tool: string;
  args: Record<string, unknown>;
  success: boolean;
  error?: string;
  durationMs: number;
}

export class AuditLog {
  private entries: AuditEntry[] = [];
  private logPath?: string;

  constructor(atlasDir?: string) {
    if (atlasDir) {
      this.logPath = path.join(atlasDir, "logs", "audit.jsonl");
    }
  }

  record(entry: AuditEntry): void {
    this.entries.push(entry);

    if (this.logPath) {
      try {
        const logDir = path.dirname(this.logPath);
        fs.mkdirSync(logDir, { recursive: true });
        fs.appendFileSync(this.logPath, JSON.stringify(entry) + "\n", "utf-8");
      } catch {
        // Silently ignore write errors — in-memory log still has the entry
      }
    }
  }

  getRecent(n = 20): AuditEntry[] {
    return this.entries.slice(-n);
  }

  clear(): void {
    this.entries = [];
  }
}
