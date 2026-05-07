import React from "react";
import { Box, Text, useInput } from "ink";
import { ToolCall } from "../tools/types.js";

interface Props {
  toolCall: ToolCall;
  reason?: string;
  onApprove: () => void;
  onDeny: () => void;
}

export function ApprovalPrompt({ toolCall, reason, onApprove, onDeny }: Props) {
  useInput((input, key) => {
    if (input.toLowerCase() === "y" || key.return) {
      onApprove();
    } else if (input.toLowerCase() === "n" || key.escape) {
      onDeny();
    }
  });

  const argsStr = JSON.stringify(toolCall.args, null, 2);
  const shortArgs =
    argsStr.length > 200 ? argsStr.slice(0, 200) + "\n  ..." : argsStr;

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor="yellow"
      paddingX={2}
      paddingY={1}
      marginY={1}
    >
      <Text color="yellow" bold>
        ⚠  Tool Approval Required
      </Text>
      {reason && (
        <Text color="yellow" dimColor>
          {reason}
        </Text>
      )}
      <Box marginTop={1} flexDirection="column">
        <Text bold>Tool: </Text>
        <Text color="cyan">{toolCall.name}</Text>
      </Box>
      <Box flexDirection="column">
        <Text bold>Args:</Text>
        <Text dimColor>{shortArgs}</Text>
      </Box>
      <Box marginTop={1}>
        <Text>Allow this tool call? </Text>
        <Text color="green" bold>
          [Y]es
        </Text>
        <Text> / </Text>
        <Text color="red" bold>
          [N]o
        </Text>
      </Box>
    </Box>
  );
}
