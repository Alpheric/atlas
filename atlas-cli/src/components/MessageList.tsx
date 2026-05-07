import React from "react";
import { Box, Text } from "ink";
import { Message } from "../api.js";
import { renderMarkdown } from "../markdown.js";

interface Props {
  messages: Message[];
  streamingText?: string;
}

function UserBubble({ content }: { content: string | null }) {
  return (
    <Box flexDirection="column" marginBottom={1}>
      <Text color="cyan" bold>
        you
      </Text>
      <Box marginLeft={2}>
        <Text>{content}</Text>
      </Box>
    </Box>
  );
}

function AssistantBubble({ content }: { content: string | null }) {
  const rendered = renderMarkdown(content ?? "");
  return (
    <Box flexDirection="column" marginBottom={1}>
      <Text color="green" bold>
        atlas
      </Text>
      <Box marginLeft={2} flexDirection="column">
        <Text>{rendered}</Text>
      </Box>
    </Box>
  );
}

export function MessageList({ messages, streamingText }: Props) {
  return (
    <Box flexDirection="column">
      {messages.map((msg, i) =>
        msg.role === "user" ? (
          <UserBubble key={i} content={msg.content} />
        ) : (
          <AssistantBubble key={i} content={msg.content} />
        )
      )}
      {streamingText !== undefined && streamingText !== "" && (
        <Box flexDirection="column" marginBottom={1}>
          <Text color="green" bold>
            atlas
          </Text>
          <Box marginLeft={2}>
            <Text>{renderMarkdown(streamingText)}</Text>
          </Box>
        </Box>
      )}
    </Box>
  );
}
