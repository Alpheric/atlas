export interface ToolDefinition {
  name: string;
  description: string;
  parameters: Record<string, { type: string; description: string; required?: boolean }>;
}

export interface ToolCall {
  name: string;
  args: Record<string, unknown>;
}

export interface ToolResult {
  success: boolean;
  output: string;
  error?: string;
}

export type ToolHandler = (
  args: Record<string, unknown>,
  workspaceRoot: string
) => Promise<ToolResult>;

// ---------------------------------------------------------------------------
// OpenAI native tool calling schema
// ---------------------------------------------------------------------------

export interface OpenAIToolFunction {
  name: string;
  description: string;
  parameters: {
    type: "object";
    properties: Record<string, { type: string; description: string }>;
    required: string[];
  };
}

export interface OpenAITool {
  type: "function";
  function: OpenAIToolFunction;
}

/** Convert our internal ToolDefinition[] to the OpenAI `tools` array format. */
export function toOpenAITools(tools: ToolDefinition[]): OpenAITool[] {
  return tools.map((t) => ({
    type: "function" as const,
    function: {
      name: t.name,
      description: t.description,
      parameters: {
        type: "object" as const,
        properties: Object.fromEntries(
          Object.entries(t.parameters).map(([k, v]) => [
            k,
            { type: v.type, description: v.description },
          ])
        ),
        required: Object.entries(t.parameters)
          .filter(([, v]) => v.required)
          .map(([k]) => k),
      },
    },
  }));
}

export const ALL_TOOL_DEFINITIONS: ToolDefinition[] = [
  {
    name: "read_file",
    description: "Read the contents of a file",
    parameters: {
      path: { type: "string", description: "Relative path to the file", required: true },
      start_line: { type: "number", description: "Optional start line (1-based)" },
      end_line: { type: "number", description: "Optional end line (1-based)" },
    },
  },
  {
    name: "write_file",
    description: "Write content to a file (creates or overwrites)",
    parameters: {
      path: { type: "string", description: "Relative path to the file", required: true },
      content: { type: "string", description: "Content to write", required: true },
    },
  },
  {
    name: "edit_file",
    description: "Replace a specific string in a file with new content",
    parameters: {
      path: { type: "string", description: "Relative path to the file", required: true },
      old_string: {
        type: "string",
        description: "Exact string to find and replace",
        required: true,
      },
      new_string: { type: "string", description: "Replacement string", required: true },
    },
  },
  {
    name: "list_files",
    description: "List files and directories in a path",
    parameters: {
      path: { type: "string", description: "Relative path to list (default: .)" },
      recursive: { type: "boolean", description: "Whether to list recursively" },
    },
  },
  {
    name: "search_files",
    description: "Find files matching a glob pattern",
    parameters: {
      pattern: {
        type: "string",
        description: "Glob pattern (e.g. src/**/*.ts)",
        required: true,
      },
      ignore: { type: "string", description: "Patterns to ignore (comma-separated)" },
    },
  },
  {
    name: "grep",
    description: "Search for a pattern in files",
    parameters: {
      pattern: {
        type: "string",
        description: "Regex or text pattern to search",
        required: true,
      },
      path: { type: "string", description: "Directory or file to search in (default: .)" },
      file_pattern: { type: "string", description: "File glob filter (e.g. *.ts)" },
    },
  },
  {
    name: "run_command",
    description: "Execute a shell command in the workspace directory",
    parameters: {
      command: { type: "string", description: "Command to run", required: true },
      timeout_ms: { type: "number", description: "Timeout in ms (default: 30000)" },
    },
  },
  {
    name: "git_status",
    description: "Get the current git status",
    parameters: {},
  },
  {
    name: "git_diff",
    description: "Get git diff (staged or unstaged)",
    parameters: {
      staged: { type: "boolean", description: "Show staged diff" },
      file: { type: "string", description: "Specific file to diff" },
    },
  },
  {
    name: "git_commit_message",
    description: "Generate a commit message based on staged changes",
    parameters: {},
  },
  {
    name: "create_directory",
    description: "Create a directory (and parents)",
    parameters: {
      path: { type: "string", description: "Relative path to create", required: true },
    },
  },
];

export function formatToolsForPrompt(tools: ToolDefinition[]): string {
  return tools
    .map((t) => {
      const params = Object.entries(t.parameters)
        .map(([k, v]) => `  ${k}${v.required ? "*" : ""} (${v.type}): ${v.description}`)
        .join("\n");
      return `### ${t.name}\n${t.description}${params ? "\nParameters:\n" + params : ""}`;
    })
    .join("\n\n");
}
