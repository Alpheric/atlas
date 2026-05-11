import React from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import { UsageInfo } from "../api.js";
import { PermissionMode } from "../permissions.js";

interface Props {
  model: string;
  baseUrl: string;
  loading: boolean;
  usage?: UsageInfo;
  totalCost?: number;   // cumulative session cost in USD
  error?: string;
  permissionMode?: PermissionMode;
  cwd?: string;
  turnCount?: number;
  gitBranch?: string;
}

const PERM_LABEL: Record<PermissionMode, string> = {
  readonly: "readonly",
  ask:      "ask",
  auto:     "auto",
  danger:   "danger",
};

const PERM_COLOR: Record<PermissionMode, Parameters<typeof Text>[0]["color"]> = {
  readonly: "blue",
  ask:      "yellow",
  auto:     "green",
  danger:   "red",
};

export function StatusBar({
  model,
  baseUrl,
  loading,
  usage,
  totalCost,
  error,
  permissionMode,
  cwd,
  turnCount,
  gitBranch,
}: Props) {
  const host = (() => {
    try { return new URL(baseUrl).host; } catch { return baseUrl; }
  })();

  // Keep path short — last 2 segments
  const shortCwd = (() => {
    if (!cwd) return "";
    const parts = cwd.replace(/\\/g, "/").split("/").filter(Boolean);
    if (parts.length <= 2) return parts.join("/");
    return "…/" + parts.slice(-2).join("/");
  })();

  const permColor = permissionMode ? PERM_COLOR[permissionMode] : undefined;
  const permLabel = permissionMode ? PERM_LABEL[permissionMode] : undefined;

  // Format token counts compactly (K suffix for thousands)
  const fmt = (n: number) => n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);

  return (
    <Box
      borderStyle="single"
      borderColor="gray"
      paddingX={1}
      justifyContent="space-between"
    >
      {/* ── Left: status + path ─────────────────────────────────────────── */}
      <Box gap={1} alignItems="center">
        {loading ? (
          <>
            <Text color="green"><Spinner type="dots" /></Text>
            <Text color="green" dimColor>thinking</Text>
          </>
        ) : error ? (
          <Text color="red" bold>✗ {error.slice(0, 50)}</Text>
        ) : (
          <Text dimColor>{model} @ {host}</Text>
        )}

        {shortCwd && (
          <>
            <Text dimColor>│</Text>
            <Text dimColor>{shortCwd}</Text>
          </>
        )}

        {gitBranch && (
          <>
            <Text dimColor>│</Text>
            <Text color="magenta" dimColor>⎇ {gitBranch}</Text>
          </>
        )}

        {turnCount != null && turnCount > 0 && !loading && (
          <>
            <Text dimColor>│</Text>
            <Text dimColor>{turnCount} turn{turnCount === 1 ? "" : "s"}</Text>
          </>
        )}
      </Box>

      {/* ── Right: permission badge + token usage ───────────────────────── */}
      <Box gap={1} alignItems="center">
        {permLabel && permColor && (
          <Text color={permColor} dimColor bold>
            [{permLabel}]
          </Text>
        )}
        {usage && !loading && (
          <Text dimColor>
            ↑{fmt(usage.inputTokens)} ↓{fmt(usage.outputTokens)} tok
          </Text>
        )}
        {totalCost != null && totalCost > 0 && !loading && (
          <Text dimColor>
            ~${totalCost < 0.01 ? totalCost.toFixed(4) : totalCost.toFixed(3)}
          </Text>
        )}
      </Box>
    </Box>
  );
}
