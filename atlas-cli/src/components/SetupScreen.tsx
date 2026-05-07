/**
 * SetupScreen — shown on first launch when no API key is configured.
 * Walks the user through entering their key and (optionally) the base URL,
 * saves to config, then calls onComplete to start the main app.
 */

import React, { useState, useCallback } from "react";
import { Box, Text, useInput, useApp } from "ink";
import { saveConfig } from "../config.js";

type Step = "key" | "url" | "done";

interface SetupScreenProps {
  defaultBaseUrl: string;
  onComplete: (apiKey: string, baseUrl: string) => void;
}

export function SetupScreen({ defaultBaseUrl, onComplete }: SetupScreenProps) {
  const { exit } = useApp();

  const [step, setStep]       = useState<Step>("key");
  const [keyInput, setKeyInput]   = useState("");
  const [urlInput, setUrlInput]   = useState(defaultBaseUrl);
  const [error, setError]     = useState("");

  useInput(useCallback((char: string, key: { return?: boolean; backspace?: boolean; delete?: boolean; escape?: boolean; ctrl?: boolean }) => {
    if (key.ctrl && char === "c") {
      exit();
      return;
    }

    if (step === "key") {
      if (key.return) {
        const trimmed = keyInput.trim();
        if (!trimmed) {
          setError("API key cannot be empty.");
          return;
        }
        setError("");
        setStep("url");
        return;
      }
      if (key.backspace || key.delete) {
        setKeyInput((v) => v.slice(0, -1));
        return;
      }
      if (char && !key.return) {
        setKeyInput((v) => v + char);
      }
      return;
    }

    if (step === "url") {
      if (key.return) {
        const trimmed = urlInput.trim() || defaultBaseUrl;
        saveConfig({ apiKey: keyInput.trim(), baseUrl: trimmed });
        setStep("done");
        // Short delay so the "Saved!" message is visible before transitioning
        setTimeout(() => onComplete(keyInput.trim(), trimmed), 600);
        return;
      }
      if (key.backspace || key.delete) {
        setUrlInput((v) => v.slice(0, -1));
        return;
      }
      if (key.escape) {
        // Reset URL to default
        setUrlInput(defaultBaseUrl);
        return;
      }
      if (char && !key.return) {
        setUrlInput((v) => v + char);
      }
    }
  }, [step, keyInput, urlInput, defaultBaseUrl, onComplete, exit]));

  // Mask the API key — show last 4 chars only
  const maskedKey = keyInput.length > 4
    ? "•".repeat(keyInput.length - 4) + keyInput.slice(-4)
    : "•".repeat(keyInput.length);

  return (
    <Box flexDirection="column" paddingX={2} paddingY={1}>

      {/* Banner */}
      <Box flexDirection="column" marginBottom={1}>
        <Text color="green" bold>
          ╔══════════════════════════════════════╗
        </Text>
        <Text color="green" bold>
          {"║  "}
          <Text color="white" bold>Atlas Code</Text>
          <Text color="green" bold>  —  Alpheric AI             ║</Text>
        </Text>
        <Text color="green" bold>
          ╚══════════════════════════════════════╝
        </Text>
      </Box>

      {step !== "done" && (
        <Box flexDirection="column" marginBottom={1}>
          <Text dimColor>Get your API key at </Text>
          <Text color="cyan">https://atlas.alpheric.ai</Text>
        </Box>
      )}

      {/* Step 1 — API key */}
      <Box flexDirection="column" marginBottom={1}>
        <Text bold color={step === "key" ? "green" : "white"}>
          {step === "key" ? "▶ " : "✓ "}
          API Key
        </Text>
        <Box marginLeft={2} marginTop={0}>
          {step === "key" ? (
            <Text>
              {maskedKey}
              <Text color="green">█</Text>
            </Text>
          ) : (
            <Text dimColor>{"•".repeat(Math.min(keyInput.length, 8))}…  (saved)</Text>
          )}
        </Box>
        {step === "key" && error ? (
          <Box marginLeft={2}><Text color="red">{error}</Text></Box>
        ) : null}
        {step === "key" && (
          <Box marginLeft={2} marginTop={0}>
            <Text dimColor>Press Enter to continue</Text>
          </Box>
        )}
      </Box>

      {/* Step 2 — Base URL */}
      {(step === "url" || step === "done") && (
        <Box flexDirection="column" marginBottom={1}>
          <Text bold color={step === "url" ? "green" : "white"}>
            {step === "url" ? "▶ " : "✓ "}
            Base URL
          </Text>
          <Box marginLeft={2}>
            <Text color={step === "url" ? "white" : "gray"}>
              {urlInput}
              {step === "url" && <Text color="green">█</Text>}
            </Text>
          </Box>
          {step === "url" && (
            <Box marginLeft={2} marginTop={0}>
              <Text dimColor>Edit or press Enter to accept · Esc to reset to default</Text>
            </Box>
          )}
        </Box>
      )}

      {/* Done */}
      {step === "done" && (
        <Box marginTop={1}>
          <Text color="green" bold>✓ Config saved — starting Atlas…</Text>
        </Box>
      )}
    </Box>
  );
}
