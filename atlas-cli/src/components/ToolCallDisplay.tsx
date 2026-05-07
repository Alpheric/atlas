import React from "react";
import { Box, Text } from "ink";
import Spinner from "ink-spinner";
import { ToolCall } from "../tools/types.js";

interface Props {
  toolCall: ToolCall;
  status: "running" | "done" | "denied" | "error";
  result?: string;
}

// Per-tool icons, short labels, and accent colours
const TOOL_META: Record<string, { icon: string; label: string; color: Parameters<typeof Text>[0]["color"] }> = {
  write_file:          { icon: "✎", label: "write",   color: "blue"    },
  edit_file:           { icon: "✏", label: "edit",    color: "blue"    },
  read_file:           { icon: "◎", label: "read",    color: "cyan"    },
  list_files:          { icon: "≡", label: "ls",      color: "cyan"    },
  search_files:        { icon: "⌕", label: "find",    color: "cyan"    },
  grep:                { icon: "⌕", label: "grep",    color: "cyan"    },
  run_command:         { icon: "▶", label: "run",     color: "yellow"  },
  create_directory:    { icon: "+", label: "mkdir",   color: "blue"    },
  git_status:          { icon: "⎇", label: "git",     color: "magenta" },
  git_diff:            { icon: "⎇", label: "diff",    color: "magenta" },
  git_commit_message:  { icon: "⎇", label: "commit",  color: "magenta" },
};

/** Build a concise, human-readable argument summary per tool type. */
function buildArgLine(name: string, args: Record<string, unknown>): string {
  const s = (v: unknown, max = 80) => {
    const str = typeof v === "string" ? v : JSON.stringify(v);
    return str.length > max ? str.slice(0, max) + "…" : str;
  };

  switch (name) {
    case "write_file":
    case "edit_file":
    case "read_file":
    case "create_directory":
      return s(args.path ?? "", 100);

    case "run_command":
      return s(args.command ?? "", 90);

    case "grep":
      return [
        args.pattern ? `/${args.pattern}/` : "",
        args.path && args.path !== "." ? `in ${args.path}` : "",
        args.file_pattern ? `(${args.file_pattern})` : "",
      ].filter(Boolean).join(" ");

    case "search_files":
      return [
        s(args.pattern ?? "", 50),
        args.path && args.path !== "." ? `in ${args.path}` : "",
      ].filter(Boolean).join(" ");

    case "git_diff":
      return [
        args.staged ? "--staged" : "",
        args.file ? String(args.file) : "",
      ].filter(Boolean).join(" ") || "(working tree)";

    default:
      return Object.entries(args)
        .map(([k, v]) => `${k}=${s(v, 40)}`)
        .slice(0, 3)
        .join("  ");
  }
}

/** Extract a one-liner from the tool result for inline display. */
function resultOneLiner(name: string, result: string): string | undefined {
  const first = result.split("\n")[0].trim();
  // Simple success messages are fine inline
  if (first.length > 0 && first.length < 80) return first;
  return undefined;
}

/** Multi-line result display with truncation. */
function ResultBlock({ result, isError }: { result: string; isError: boolean }) {
  const lines = result.trimEnd().split("\n");
  const MAX = 10;
  const shown = lines.slice(0, MAX);
  const extra = lines.length - MAX;

  return (
    <Box flexDirection="column" marginLeft={4}>
      {shown.map((line, i) => (
        <Text key={i} color={isError ? "red" : undefined} dimColor={!isError}>
          {line}
        </Text>
      ))}
      {extra > 0 && (
        <Text dimColor>… {extra} more line{extra === 1 ? "" : "s"}</Text>
      )}
    </Box>
  );
}

export function ToolCallDisplay({ toolCall, status, result }: Props) {
  const meta = TOOL_META[toolCall.name] ?? {
    icon: "⚙",
    label: toolCall.name,
    color: "white" as Parameters<typeof Text>[0]["color"],
  };

  const argLine = buildArgLine(toolCall.name, toolCall.args);

  // Decide how to display the result
  const oneLiner = result && status === "done"
    ? resultOneLiner(toolCall.name, result)
    : undefined;

  const showBlock =
    result &&
    (status === "error" ||
      (status === "done" && !oneLiner && result.trim().length > 0));

  return (
    <Box flexDirection="column">
      {/* ── Main row ── */}
      <Box gap={1} alignItems="flex-start">
        {/* Status/spinner */}
        <Box width={2}>
          {status === "running" ? (
            <Text color="yellow"><Spinner type="dots" /></Text>
          ) : status === "done" ? (
            <Text color="green">✓</Text>
          ) : (
            <Text color="red">✗</Text>
          )}
        </Box>

        {/* Tool identity */}
        <Text color={meta.color} bold>
          {meta.icon}
        </Text>
        <Text color={meta.color} bold dimColor>
          {meta.label}
        </Text>

        {/* Args */}
        {argLine && (
          <Text color="white">{argLine}</Text>
        )}

        {/* Inline one-liner result (e.g. "Written 420 bytes") */}
        {oneLiner && (
          <Text dimColor>→ {oneLiner}</Text>
        )}

        {/* Denied reason */}
        {status === "denied" && result && (
          <Text color="red" dimColor>{result}</Text>
        )}
      </Box>

      {/* ── Result block (errors or verbose outputs) ── */}
      {showBlock && result && (
        <ResultBlock result={result} isError={status === "error"} />
      )}
    </Box>
  );
}
