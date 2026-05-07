/**
 * Atlas CLI configuration — persisted in ~/.config/atlas-cli/config.json
 */

import { homedir } from "os";
import { join } from "path";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "fs";

const CONFIG_DIR = join(homedir(), ".config", "atlas-cli");
const CONFIG_FILE = join(CONFIG_DIR, "config.json");

export interface AtlasConfig {
  apiKey: string;
  baseUrl: string;
  model: string;
  stream: boolean;
}

const DEFAULTS: AtlasConfig = {
  apiKey: process.env.ATLAS_API_KEY || process.env.ALPHERIC_API_KEY || "",
  baseUrl:
    process.env.ATLAS_BASE_URL ||
    process.env.ALPHERIC_BASE_URL ||
    "https://atlas.alpheric.ai/v1",
  model: process.env.ATLAS_MODEL || "atlas-code",
  stream: true,
};

function ensureConfigDir(): void {
  if (!existsSync(CONFIG_DIR)) {
    mkdirSync(CONFIG_DIR, { recursive: true });
  }
}

export function loadConfig(): AtlasConfig {
  ensureConfigDir();
  if (existsSync(CONFIG_FILE)) {
    try {
      const raw = readFileSync(CONFIG_FILE, "utf-8");
      const saved = JSON.parse(raw);
      return { ...DEFAULTS, ...saved };
    } catch {
      // Corrupt config — use defaults
    }
  }
  return { ...DEFAULTS };
}

export function saveConfig(config: Partial<AtlasConfig>): void {
  ensureConfigDir();
  const current = loadConfig();
  const updated = { ...current, ...config };
  writeFileSync(CONFIG_FILE, JSON.stringify(updated, null, 2));
}

export function getConfigPath(): string {
  return CONFIG_FILE;
}
