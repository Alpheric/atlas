import React from "react";
import { Box, Text } from "ink";

interface Props {
  diff: string;
  title?: string;
}

/** Parse a unified diff header to extract filenames. */
function parseHeader(lines: string[]): { from: string; to: string } | null {
  const minus = lines.find(l => l.startsWith("---"));
  const plus  = lines.find(l => l.startsWith("+++"));
  if (!minus || !plus) return null;
  return {
    from: minus.replace(/^---\s+(a\/)?/, "").split("\t")[0],
    to:   plus .replace(/^\+\+\+\s+(b\/)?/, "").split("\t")[0],
  };
}

/** Count added/removed lines in the diff. */
function countChanges(lines: string[]): { added: number; removed: number } {
  let added = 0, removed = 0;
  for (const l of lines) {
    if (l.startsWith("+") && !l.startsWith("+++")) added++;
    else if (l.startsWith("-") && !l.startsWith("---")) removed++;
  }
  return { added, removed };
}

export function DiffViewer({ diff, title }: Props) {
  const lines = diff.split("\n");
  const header = parseHeader(lines);
  const { added, removed } = countChanges(lines);

  // Body lines — skip --- / +++ file headers (shown in our own header)
  const bodyLines = lines.filter(l => !l.startsWith("---") && !l.startsWith("+++"));

  // Limit display to keep it manageable
  const MAX_LINES = 60;
  const truncated = bodyLines.length > MAX_LINES;
  const displayLines = truncated ? bodyLines.slice(0, MAX_LINES) : bodyLines;

  const fileLabel = header
    ? (header.from === header.to ? header.to : `${header.from} → ${header.to}`)
    : (title ?? "diff");

  return (
    <Box flexDirection="column" marginY={1}>
      {/* ── Diff header ── */}
      <Box gap={2} paddingX={1}>
        <Text color="cyan" bold>⎇ {fileLabel}</Text>
        {(added > 0 || removed > 0) && (
          <Box gap={1}>
            {added > 0   && <Text color="green">+{added}</Text>}
            {removed > 0 && <Text color="red">−{removed}</Text>}
          </Box>
        )}
      </Box>

      {/* ── Diff body ── */}
      <Box
        flexDirection="column"
        borderStyle="single"
        borderColor="gray"
        paddingX={1}
        marginTop={0}
      >
        {displayLines.map((line, i) => {
          const clean = line.replace(/\x1b\[[0-9;]*m/g, "");

          if (clean.startsWith("@@")) {
            return (
              <Text key={i} color="cyan" dimColor>
                {clean}
              </Text>
            );
          }
          if (clean.startsWith("+")) {
            return (
              <Box key={i}>
                <Text color="green">{clean}</Text>
              </Box>
            );
          }
          if (clean.startsWith("-")) {
            return (
              <Box key={i}>
                <Text color="red">{clean}</Text>
              </Box>
            );
          }
          return (
            <Text key={i} dimColor>
              {clean}
            </Text>
          );
        })}

        {truncated && (
          <Text dimColor>… {bodyLines.length - MAX_LINES} more lines (diff truncated)</Text>
        )}
      </Box>
    </Box>
  );
}
