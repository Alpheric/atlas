import fs from "fs";
import path from "path";

export interface WorkspaceInfo {
  cwd: string;
  isGit: boolean;
  gitRoot?: string;
  packageManager?: "npm" | "pnpm" | "yarn" | "bun";
  framework?: string;
  detectedFiles: string[]; // important files found (relative paths)
  atlasConfigDir?: string; // path to .atlas/ if it exists
  hasAtlasMd: boolean;
  hasMemoryMd: boolean;
}

function exists(p: string): boolean {
  try {
    fs.accessSync(p);
    return true;
  } catch {
    return false;
  }
}

function findGitRoot(startDir: string, maxLevels = 5): string | undefined {
  let dir = startDir;
  for (let i = 0; i < maxLevels; i++) {
    if (exists(path.join(dir, ".git"))) {
      return dir;
    }
    const parent = path.dirname(dir);
    if (parent === dir) break; // reached filesystem root
    dir = parent;
  }
  return undefined;
}

function detectPackageManager(cwd: string): WorkspaceInfo["packageManager"] {
  if (exists(path.join(cwd, "bun.lockb"))) return "bun";
  if (exists(path.join(cwd, "pnpm-lock.yaml"))) return "pnpm";
  if (exists(path.join(cwd, "yarn.lock"))) return "yarn";
  if (exists(path.join(cwd, "package-lock.json"))) return "npm";
  return undefined;
}

function detectFramework(cwd: string): string | undefined {
  // Check for framework config files first
  if (
    exists(path.join(cwd, "next.config.js")) ||
    exists(path.join(cwd, "next.config.ts")) ||
    exists(path.join(cwd, "next.config.mjs"))
  ) {
    return "Next.js";
  }

  if (
    exists(path.join(cwd, "vite.config.ts")) ||
    exists(path.join(cwd, "vite.config.js"))
  ) {
    return "Vite";
  }

  // Check package.json for dependencies
  const pkgPath = path.join(cwd, "package.json");
  if (exists(pkgPath)) {
    try {
      const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf-8"));
      const deps: Record<string, string> = {
        ...(pkg.dependencies ?? {}),
        ...(pkg.devDependencies ?? {}),
      };
      const depNames = Object.keys(deps);

      if (depNames.some((d) => d === "fastapi" || d === "uvicorn")) return "FastAPI";
      if (depNames.some((d) => d.includes("nestjs") || d === "@nestjs/core")) return "NestJS";
      if (depNames.some((d) => d === "laravel-vite-plugin" || d === "laravel")) return "Laravel";
      if (depNames.includes("react")) return "React";
    } catch {
      // ignore malformed package.json
    }
  }

  // Python project detection (no package.json)
  if (!exists(pkgPath)) {
    if (exists(path.join(cwd, "pyproject.toml")) || exists(path.join(cwd, "requirements.txt"))) {
      return "Python";
    }
  }

  return undefined;
}

const IMPORTANT_FILES = [
  "README.md",
  "package.json",
  "tsconfig.json",
  "next.config.js",
  "next.config.ts",
  "next.config.mjs",
  "vite.config.ts",
  "vite.config.js",
  "Dockerfile",
  "docker-compose.yml",
  "docker-compose.yaml",
  ".env.example",
  "prisma/schema.prisma",
  "pyproject.toml",
  "requirements.txt",
  "ATLAS.md",
  ".atlas/settings.json",
];

export async function detectWorkspace(): Promise<WorkspaceInfo> {
  const cwd = process.cwd();

  const gitRoot = findGitRoot(cwd);
  const isGit = gitRoot !== undefined;

  const packageManager = detectPackageManager(cwd);
  const framework = detectFramework(cwd);

  const detectedFiles = IMPORTANT_FILES.filter((f) => exists(path.join(cwd, f)));

  const atlasDirPath = path.join(cwd, ".atlas");
  const atlasConfigDir = exists(atlasDirPath) ? atlasDirPath : undefined;

  const hasAtlasMd = exists(path.join(cwd, "ATLAS.md"));
  const hasMemoryMd = exists(path.join(cwd, ".atlas", "memory.md"));

  return {
    cwd,
    isGit,
    gitRoot,
    packageManager,
    framework,
    detectedFiles,
    atlasConfigDir,
    hasAtlasMd,
    hasMemoryMd,
  };
}
