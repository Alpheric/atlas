import React from "react";
import { Box, Text } from "ink";

// "ATLAS" in block art — 45 chars wide, looks great at 80+ col terminals
const ART = [
  "  ██████╗ ████████╗██╗      █████╗ ███████╗",
  " ██╔══██╗╚══██╔══╝██║     ██╔══██╗██╔════╝",
  " ███████║   ██║   ██║     ███████║███████╗ ",
  " ██╔══██║   ██║   ██║     ██╔══██║╚════██║ ",
  " ██║  ██║   ██║   ███████╗██║  ██║███████║ ",
  " ╚═╝  ╚═╝   ╚═╝   ╚══════╝╚═╝  ╚═╝╚══════╝",
];

// Row colours — gradient from bright to dimmer green → cyan accent on last row
const ART_COLORS = [
  "greenBright",
  "greenBright",
  "green",
  "green",
  "cyan",
  "cyan",
] as const;

interface Props {
  model: string;
  baseUrl: string;
  workspace?: string;
  /** When true, render the single-line compact header instead of the big banner. */
  compact?: boolean;
  agentEnabled?: boolean;
}

export function Banner({ model, baseUrl, workspace, compact, agentEnabled }: Props) {
  const host = (() => {
    try { return new URL(baseUrl).host; } catch { return baseUrl; }
  })();

  // ── Compact header (after first message) ─────────────────────────────────
  if (compact) {
    return (
      <Box
        borderStyle="single"
        borderColor="green"
        paddingX={1}
        justifyContent="space-between"
      >
        <Box gap={1}>
          <Text color="greenBright" bold>ATLAS</Text>
          <Text color="cyan" dimColor>by Alpheric AI</Text>
          <Text dimColor>│</Text>
          <Text color="green" dimColor>{model}{agentEnabled ? " [agent]" : ""}</Text>
        </Box>
        <Text dimColor>/help · ↑↓ history · Ctrl+C quit</Text>
      </Box>
    );
  }

  // ── Full banner (startup / no messages yet) ───────────────────────────────
  return (
    <Box flexDirection="column" marginBottom={1}>
      {/* ASCII art block */}
      <Box flexDirection="column" alignItems="center" paddingTop={1}>
        {ART.map((line, i) => (
          <Text key={i} color={ART_COLORS[i]} bold>
            {line}
          </Text>
        ))}
      </Box>

      {/* Subtitle */}
      <Box justifyContent="center" marginTop={1}>
        <Text color="cyan" bold>  ✦  by Alpheric AI  ✦  </Text>
      </Box>
      <Box justifyContent="center">
        <Text dimColor>Agentic AI coding assistant for the terminal</Text>
      </Box>

      {/* Divider */}
      <Box justifyContent="center" marginTop={1}>
        <Text dimColor>{"─".repeat(46)}</Text>
      </Box>

      {/* Session info */}
      <Box justifyContent="center" gap={4} marginTop={1}>
        <Box gap={1}>
          <Text dimColor>model</Text>
          <Text color="green">{model}{agentEnabled ? " [agent]" : ""}</Text>
        </Box>
        <Box gap={1}>
          <Text dimColor>host</Text>
          <Text color="green">{host}</Text>
        </Box>
        {workspace && (
          <Box gap={1}>
            <Text dimColor>dir</Text>
            <Text color="green">{workspace}</Text>
          </Box>
        )}
      </Box>

      {/* Quick-start key hints */}
      <Box justifyContent="center" gap={3} marginTop={1} marginBottom={1}>
        <Text dimColor><Text color="cyan">/help</Text> commands</Text>
        <Text dimColor><Text color="cyan">/mode</Text> auto|ask</Text>
        <Text dimColor><Text color="cyan">↑↓</Text> history</Text>
        <Text dimColor><Text color="cyan">Ctrl+W</Text> del word</Text>
        <Text dimColor><Text color="cyan">Ctrl+C</Text> quit</Text>
      </Box>
    </Box>
  );
}
