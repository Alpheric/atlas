import React, { useState } from "react";
import { Box, Text, useInput } from "ink";

// ─────────────────────────────────────────────────────────────────────────────
// Slash-command registry (for inline autocomplete)
// ─────────────────────────────────────────────────────────────────────────────

const SLASH_CMDS: { cmd: string; desc: string }[] = [
  { cmd: "help",        desc: "Show all commands" },
  { cmd: "clear",       desc: "Clear conversation history" },
  { cmd: "compact",     desc: "Summarise context to save tokens" },
  { cmd: "model",       desc: "Show or switch model" },
  { cmd: "permissions", desc: "Show or set permission mode" },
  { cmd: "status",      desc: "Workspace & token usage info" },
  { cmd: "doctor",      desc: "Check configuration health" },
  { cmd: "init",        desc: "Initialise .atlas/ workspace" },
  { cmd: "read",        desc: "Read a file into context" },
  { cmd: "add",         desc: "Inject file(s) via glob" },
  { cmd: "edit",        desc: "Edit a file" },
  { cmd: "write",       desc: "Write a new file" },
  { cmd: "rollback",    desc: "Undo last file changes" },
  { cmd: "run",         desc: "Run a shell command" },
  { cmd: "fix",         desc: "Auto-fix build/test errors (up to 5×)" },
  { cmd: "git",         desc: "Show git status" },
  { cmd: "diff",        desc: "Show git diff" },
  { cmd: "review",      desc: "Review staged changes" },
  { cmd: "commit",      desc: "Generate commit message + commit" },
  { cmd: "pr",          desc: "Generate PR description" },
  { cmd: "memory",      desc: "Show or force-save memory" },
  { cmd: "copy",        desc: "Copy last response to clipboard" },
  { cmd: "history",     desc: "List saved sessions" },
  { cmd: "resume",      desc: "Restore a saved session by index" },
  { cmd: "plan",        desc: "Create an implementation plan" },
  { cmd: "security",    desc: "Security review of the codebase" },
  { cmd: "docs",        desc: "Generate docs for a file" },
  { cmd: "exit",        desc: "Exit Atlas" },
];

const MAX_SUGGESTIONS = 6;

// ─────────────────────────────────────────────────────────────────────────────

interface Props {
  onSubmit: (text: string) => void;
  disabled?: boolean;
  placeholder?: string;
  onValueChange?: (value: string) => void;
}

export function InputBox({ onSubmit, disabled, placeholder, onValueChange }: Props) {
  const [value,      setValue]      = useState("");
  const [cursorPos,  setCursorPos]  = useState(0);
  const [history,    setHistory]    = useState<string[]>([]);
  const [historyIdx, setHistoryIdx] = useState(-1);
  const [draft,      setDraft]      = useState("");

  // Solid block cursor — no blink (cleaner, less visual noise)
  const cursorOn = true;

  // ── Slash suggestions ──────────────────────────────────────────────────────
  const suggestions = value.startsWith("/")
    ? SLASH_CMDS.filter(({ cmd }) => cmd.startsWith(value.slice(1).toLowerCase())).slice(0, MAX_SUGGESTIONS)
    : [];

  // ── Key input ──────────────────────────────────────────────────────────────
  useInput(
    (input, key) => {
      if (disabled) return;

      if (key.return) {
        if (key.shift) {
          const next = value.slice(0, cursorPos) + "\n" + value.slice(cursorPos);
          setValue(next); onValueChange?.(next);
          setCursorPos(cursorPos + 1); setHistoryIdx(-1); return;
        }
        const trimmed = value.trim();
        if (!trimmed) return;
        onSubmit(trimmed);
        setHistory(prev =>
          prev.length > 0 && prev[prev.length - 1] === trimmed ? prev : [...prev, trimmed]
        );
        setHistoryIdx(-1); setDraft(""); setValue(""); onValueChange?.(""); setCursorPos(0);
        return;
      }

      if (key.upArrow) {
        if (history.length === 0) return;
        const nextIdx = historyIdx + 1;
        if (nextIdx >= history.length) return;
        if (historyIdx === -1) setDraft(value);
        const entry = history[history.length - 1 - nextIdx];
        setValue(entry); setCursorPos(entry.length); setHistoryIdx(nextIdx); return;
      }
      if (key.downArrow) {
        if (historyIdx <= 0) { setValue(draft); setCursorPos(draft.length); setHistoryIdx(-1); }
        else {
          const nextIdx = historyIdx - 1;
          const entry = history[history.length - 1 - nextIdx];
          setValue(entry); setCursorPos(entry.length); setHistoryIdx(nextIdx);
        }
        return;
      }

      if (key.backspace || key.delete) {
        if (cursorPos > 0) {
          const next = value.slice(0, cursorPos - 1) + value.slice(cursorPos);
          setValue(next); onValueChange?.(next);
          setCursorPos(cursorPos - 1); setHistoryIdx(-1);
        }
        return;
      }

      if (key.leftArrow)  { setCursorPos(Math.max(0, cursorPos - 1)); return; }
      if (key.rightArrow) { setCursorPos(Math.min(value.length, cursorPos + 1)); return; }

      if (key.ctrl) {
        switch (input) {
          case "a": setCursorPos(0); return;
          case "e": setCursorPos(value.length); return;
          case "u":
            setValue(""); onValueChange?.(""); setCursorPos(0);
            setHistoryIdx(-1); setDraft(""); return;
          case "k":
            setValue(value.slice(0, cursorPos)); onValueChange?.(value.slice(0, cursorPos)); return;
          case "w": {
            if (cursorPos === 0) return;
            const bef = value.slice(0, cursorPos).trimEnd();
            const ls  = bef.lastIndexOf(" ");
            const nb  = ls >= 0 ? bef.slice(0, ls + 1) : "";
            setValue(nb + value.slice(cursorPos)); onValueChange?.(nb + value.slice(cursorPos));
            setCursorPos(nb.length); setHistoryIdx(-1); return;
          }
        }
      }

      if (!key.ctrl && !key.meta && input) {
        const next = value.slice(0, cursorPos) + input + value.slice(cursorPos);
        setValue(next); onValueChange?.(next);
        setCursorPos(cursorPos + input.length);
        if (historyIdx !== -1) setHistoryIdx(-1);
      }
    },
    { isActive: !disabled }
  );

  // ── Render ─────────────────────────────────────────────────────────────────
  const isBrowsing  = historyIdx >= 0;
  const borderColor = disabled ? "gray" : isBrowsing ? "yellow" : "cyan";
  const promptColor = disabled ? "gray" : isBrowsing ? "yellow" : "cyan";
  const cursorBg    = isBrowsing ? "yellow" : "cyan";

  const before   = value.slice(0, cursorPos);
  const atCursor = value[cursorPos] ?? " ";
  const after    = value.slice(cursorPos + 1);

  return (
    <Box flexDirection="column">

      {/* ── Slash suggestions ── */}
      {suggestions.length > 0 && (
        <Box flexDirection="column" paddingLeft={2}>
          {suggestions.map(({ cmd, desc }) => (
            <Box key={cmd} gap={2}>
              <Text color="cyan" bold>{"/" + cmd}</Text>
              <Text dimColor>{desc}</Text>
            </Box>
          ))}
        </Box>
      )}

      {/* ── Input border ── */}
      <Box
        borderStyle="round"
        borderColor={borderColor as Parameters<typeof Box>[0]["borderColor"]}
        paddingX={1}
      >
        <Text color={promptColor as Parameters<typeof Text>[0]["color"]} bold>{"❯ "}</Text>

        {value === "" && !disabled ? (
          <>
            {cursorOn
              ? <Text backgroundColor={cursorBg} color="black">{" "}</Text>
              : <Text>{" "}</Text>}
            <Text dimColor>{placeholder ?? "Message Atlas… (↑↓ history · /help)"}</Text>
          </>
        ) : (
          <>
            <Text color={isBrowsing ? "yellow" : undefined}>{before}</Text>
            {cursorOn
              ? <Text backgroundColor={cursorBg} color="black">{atCursor}</Text>
              : <Text color={isBrowsing ? "yellow" : undefined}>{atCursor}</Text>}
            <Text color={isBrowsing ? "yellow" : undefined}>{after}</Text>
          </>
        )}
      </Box>

      {/* ── History indicator ── */}
      {isBrowsing && (
        <Box paddingLeft={3}>
          <Text dimColor>
            {`history [${history.length - historyIdx}/${history.length}]  ↑ older · ↓ newer · ↵ send · type to edit`}
          </Text>
        </Box>
      )}
    </Box>
  );
}
