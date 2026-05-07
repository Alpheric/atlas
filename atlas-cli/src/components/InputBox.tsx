import React, { useState } from "react";
import { Box, Text, useInput } from "ink";

interface Props {
  onSubmit: (text: string) => void;
  disabled?: boolean;
  placeholder?: string;
}

export function InputBox({ onSubmit, disabled, placeholder }: Props) {
  const [value, setValue] = useState("");
  const [cursorPos, setCursorPos] = useState(0);

  // Message history — stores submitted messages, newest last
  const [history, setHistory] = useState<string[]>([]);
  // historyIdx: -1 = not browsing, 0 = most recent, 1 = one before, etc.
  const [historyIdx, setHistoryIdx] = useState(-1);
  // Saved draft before entering history navigation
  const [draft, setDraft] = useState("");

  useInput(
    (input, key) => {
      if (disabled) return;

      // ── Submit ─────────────────────────────────────────────────────────────
      if (key.return) {
        const trimmed = value.trim();
        if (!trimmed) return;
        onSubmit(trimmed);
        // Push to history (dedup consecutive same message)
        setHistory(prev => {
          if (prev.length > 0 && prev[prev.length - 1] === trimmed) return prev;
          return [...prev, trimmed];
        });
        setHistoryIdx(-1);
        setDraft("");
        setValue("");
        setCursorPos(0);
        return;
      }

      // ── History navigation ─────────────────────────────────────────────────
      if (key.upArrow) {
        if (history.length === 0) return;
        const nextIdx = historyIdx + 1;
        if (nextIdx >= history.length) return; // already at oldest
        if (historyIdx === -1) setDraft(value); // save current draft
        const entry = history[history.length - 1 - nextIdx];
        setValue(entry);
        setCursorPos(entry.length);
        setHistoryIdx(nextIdx);
        return;
      }

      if (key.downArrow) {
        if (historyIdx <= 0) {
          // Return to draft
          setValue(draft);
          setCursorPos(draft.length);
          setHistoryIdx(-1);
        } else {
          const nextIdx = historyIdx - 1;
          const entry = history[history.length - 1 - nextIdx];
          setValue(entry);
          setCursorPos(entry.length);
          setHistoryIdx(nextIdx);
        }
        return;
      }

      // ── Backspace / Delete ─────────────────────────────────────────────────
      if (key.backspace || key.delete) {
        if (cursorPos > 0) {
          const next = value.slice(0, cursorPos - 1) + value.slice(cursorPos);
          setValue(next);
          setCursorPos(cursorPos - 1);
          setHistoryIdx(-1); // exit history on edit
        }
        return;
      }

      // ── Cursor movement ────────────────────────────────────────────────────
      if (key.leftArrow) {
        setCursorPos(Math.max(0, cursorPos - 1));
        return;
      }
      if (key.rightArrow) {
        setCursorPos(Math.min(value.length, cursorPos + 1));
        return;
      }

      // ── Ctrl shortcuts ─────────────────────────────────────────────────────
      if (key.ctrl) {
        switch (input) {
          case "a": // go to start
            setCursorPos(0);
            return;
          case "e": // go to end
            setCursorPos(value.length);
            return;
          case "u": // clear line
            setValue("");
            setCursorPos(0);
            setHistoryIdx(-1);
            setDraft("");
            return;
          case "k": // delete to end of line
            setValue(value.slice(0, cursorPos));
            return;
          case "w": { // delete previous word (like bash)
            if (cursorPos === 0) return;
            const before = value.slice(0, cursorPos);
            // trim trailing spaces, then cut to previous space
            const trimmedBefore = before.trimEnd();
            const lastSpace = trimmedBefore.lastIndexOf(" ");
            const newBefore = lastSpace >= 0 ? trimmedBefore.slice(0, lastSpace + 1) : "";
            setValue(newBefore + value.slice(cursorPos));
            setCursorPos(newBefore.length);
            setHistoryIdx(-1);
            return;
          }
        }
      }

      // ── Regular character input ────────────────────────────────────────────
      if (!key.ctrl && !key.meta && input) {
        const next = value.slice(0, cursorPos) + input + value.slice(cursorPos);
        setValue(next);
        setCursorPos(cursorPos + input.length);
        // Typing exits history navigation, keeping the selected text as new draft
        if (historyIdx !== -1) {
          setHistoryIdx(-1);
        }
      }
    },
    { isActive: !disabled }
  );

  // ── Render ─────────────────────────────────────────────────────────────────
  const isBrowsing = historyIdx >= 0;
  const borderColor = disabled ? "gray" : isBrowsing ? "yellow" : "cyan";
  const promptColor = disabled ? "gray" : isBrowsing ? "yellow" : "cyan";

  // Split value at cursor for rendering
  const before = value.slice(0, cursorPos);
  const atCursor = value[cursorPos] ?? " ";
  const after = value.slice(cursorPos + 1);

  return (
    <Box flexDirection="column">
      <Box
        borderStyle="round"
        borderColor={borderColor as Parameters<typeof Box>[0]["borderColor"]}
        paddingX={1}
      >
        <Text color={promptColor as Parameters<typeof Text>[0]["color"]} bold>
          {"❯ "}
        </Text>
        {value === "" && !disabled ? (
          // Empty input — show placeholder + cursor
          <>
            <Text dimColor>
              {placeholder ?? "Message Atlas… (↑↓ history · /help for commands)"}
            </Text>
            <Text backgroundColor="cyan" color="black">{" "}</Text>
          </>
        ) : (
          // Input value with block cursor
          <>
            <Text color={isBrowsing ? "yellow" : undefined}>{before}</Text>
            <Text backgroundColor={isBrowsing ? "yellow" : "cyan"} color="black">
              {atCursor}
            </Text>
            <Text color={isBrowsing ? "yellow" : undefined}>{after}</Text>
          </>
        )}
      </Box>

      {/* History indicator shown while browsing */}
      {isBrowsing && (
        <Box paddingLeft={3}>
          <Text dimColor>
            history [{history.length - historyIdx}/{history.length}]  ↑ older · ↓ newer · ↵ send · type to edit
          </Text>
        </Box>
      )}
    </Box>
  );
}
