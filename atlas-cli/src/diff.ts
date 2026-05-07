// ANSI escape codes
const ANSI_GREEN = "\x1b[32m";
const ANSI_RED = "\x1b[31m";
const ANSI_GRAY = "\x1b[90m";
const ANSI_RESET = "\x1b[0m";

export interface FileDiff {
  path: string;
  oldContent: string;
  newContent: string;
  hunks: DiffHunk[];
}

export interface DiffHunk {
  oldStart: number;
  newStart: number;
  lines: DiffLine[];
}

export interface DiffLine {
  type: "context" | "add" | "remove";
  content: string;
  lineNum?: number;
}

// Simple line-by-line diff with context grouping
export function computeDiff(
  oldContent: string,
  newContent: string,
  filePath: string
): FileDiff {
  const CONTEXT_LINES = 3;

  const oldLines = oldContent.split("\n");
  const newLines = newContent.split("\n");

  // Build a simple edit script: for each old line, find if it appears at the
  // same position in new. We use a straightforward O(n*m) LCS approach for
  // small files; for large files we fall back to a line-by-line positional diff.
  const lcs = computeLCS(oldLines, newLines);

  // Convert LCS to edit operations
  type EditOp = { type: "context" | "add" | "remove"; oldIdx?: number; newIdx?: number };
  const ops: EditOp[] = [];

  let oi = 0; // old index
  let ni = 0; // new index

  for (const [lcsOld, lcsNew] of lcs) {
    // Lines between previous LCS point and this LCS point
    while (oi < lcsOld) {
      ops.push({ type: "remove", oldIdx: oi });
      oi++;
    }
    while (ni < lcsNew) {
      ops.push({ type: "add", newIdx: ni });
      ni++;
    }
    ops.push({ type: "context", oldIdx: oi, newIdx: ni });
    oi++;
    ni++;
  }
  // Remaining lines after last LCS match
  while (oi < oldLines.length) {
    ops.push({ type: "remove", oldIdx: oi });
    oi++;
  }
  while (ni < newLines.length) {
    ops.push({ type: "add", newIdx: ni });
    ni++;
  }

  // Group into hunks (changed regions + CONTEXT_LINES surrounding context)
  const hunks: DiffHunk[] = [];
  const changedOpIndices = ops
    .map((op, i) => ({ op, i }))
    .filter(({ op }) => op.type !== "context")
    .map(({ i }) => i);

  if (changedOpIndices.length === 0) {
    return { path: filePath, oldContent, newContent, hunks: [] };
  }

  // Expand each changed region with context lines, merge overlapping regions
  const regions: Array<[number, number]> = [];
  for (const ci of changedOpIndices) {
    const start = Math.max(0, ci - CONTEXT_LINES);
    const end = Math.min(ops.length - 1, ci + CONTEXT_LINES);
    if (regions.length > 0 && start <= regions[regions.length - 1][1] + 1) {
      regions[regions.length - 1][1] = end;
    } else {
      regions.push([start, end]);
    }
  }

  for (const [regionStart, regionEnd] of regions) {
    const hunkOps = ops.slice(regionStart, regionEnd + 1);
    if (hunkOps.length === 0) continue;

    const firstOldIdx = hunkOps.find((o) => o.oldIdx !== undefined)?.oldIdx ?? 0;
    const firstNewIdx = hunkOps.find((o) => o.newIdx !== undefined)?.newIdx ?? 0;

    const lines: DiffLine[] = hunkOps.map((op) => {
      if (op.type === "remove") {
        return {
          type: "remove" as const,
          content: oldLines[op.oldIdx!] ?? "",
          lineNum: (op.oldIdx ?? 0) + 1,
        };
      } else if (op.type === "add") {
        return {
          type: "add" as const,
          content: newLines[op.newIdx!] ?? "",
          lineNum: (op.newIdx ?? 0) + 1,
        };
      } else {
        return {
          type: "context" as const,
          content: oldLines[op.oldIdx!] ?? "",
          lineNum: (op.oldIdx ?? 0) + 1,
        };
      }
    });

    hunks.push({
      oldStart: firstOldIdx + 1,
      newStart: firstNewIdx + 1,
      lines,
    });
  }

  return { path: filePath, oldContent, newContent, hunks };
}

// Patience/simple LCS: returns array of [oldIndex, newIndex] matched pairs
function computeLCS(oldLines: string[], newLines: string[]): Array<[number, number]> {
  const m = oldLines.length;
  const n = newLines.length;

  // For performance, limit to 500 lines each direction
  if (m > 500 || n > 500) {
    return simplePairDiff(oldLines, newLines);
  }

  // Standard DP LCS
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));

  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (oldLines[i - 1] === newLines[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }

  // Backtrack
  const result: Array<[number, number]> = [];
  let i = m;
  let j = n;
  while (i > 0 && j > 0) {
    if (oldLines[i - 1] === newLines[j - 1]) {
      result.push([i - 1, j - 1]);
      i--;
      j--;
    } else if (dp[i - 1][j] > dp[i][j - 1]) {
      i--;
    } else {
      j--;
    }
  }

  return result.reverse();
}

// Fallback for large files: positional matching only
function simplePairDiff(oldLines: string[], newLines: string[]): Array<[number, number]> {
  const result: Array<[number, number]> = [];
  const minLen = Math.min(oldLines.length, newLines.length);
  for (let i = 0; i < minLen; i++) {
    if (oldLines[i] === newLines[i]) {
      result.push([i, i]);
    }
  }
  return result;
}

export function formatDiff(diff: FileDiff): string {
  if (diff.hunks.length === 0) {
    return `--- ${diff.path}\n+++ ${diff.path}\n(no changes)`;
  }

  const out: string[] = [];
  out.push(`${ANSI_GRAY}--- ${diff.path}${ANSI_RESET}`);
  out.push(`${ANSI_GRAY}+++ ${diff.path}${ANSI_RESET}`);

  for (const hunk of diff.hunks) {
    // Compute hunk header counts
    const oldCount = hunk.lines.filter((l) => l.type !== "add").length;
    const newCount = hunk.lines.filter((l) => l.type !== "remove").length;
    out.push(
      `${ANSI_GRAY}@@ -${hunk.oldStart},${oldCount} +${hunk.newStart},${newCount} @@${ANSI_RESET}`
    );

    for (const line of hunk.lines) {
      if (line.type === "add") {
        out.push(`${ANSI_GREEN}+${line.content}${ANSI_RESET}`);
      } else if (line.type === "remove") {
        out.push(`${ANSI_RED}-${line.content}${ANSI_RESET}`);
      } else {
        out.push(`${ANSI_GRAY} ${line.content}${ANSI_RESET}`);
      }
    }
  }

  return out.join("\n");
}

export function applyEdit(
  content: string,
  oldString: string,
  newString: string
): string | null {
  const idx = content.indexOf(oldString);
  if (idx === -1) return null;
  return content.slice(0, idx) + newString + content.slice(idx + oldString.length);
}
