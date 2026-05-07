# Atlas Code CLI

An agentic terminal coding assistant for [Alpheric Atlas](https://atlas.alpheric.ai). Chat, inspect files, edit code, run commands, and review git changes — all from your terminal, with streaming responses and full safety controls.

## Quick Start

```bash
# Set your API key (one time)
atlas config set apiKey sk-atlas-YOUR_KEY

# Initialise workspace (creates ATLAS.md + .atlas/)
cd your-project
atlas init

# Start the agent
atlas
```

## What It Does

- **Agentic coding** — Atlas can read files, edit code, run commands, and search your codebase autonomously
- **Permission system** — choose `readonly`, `ask`, `auto`, or `danger` mode; approve each tool call before it runs
- **Workspace detection** — auto-detects git, framework, package manager, and key project files
- **Project context** — reads `ATLAS.md` and `.atlas/memory.md` into every session automatically
- **Streaming responses** — token-by-token output with diff previews before file edits
- **Slash commands** — `/help`, `/status`, `/git`, `/diff`, `/plan`, `/security`, `/review`, and more
- **Custom commands** — add `.atlas/commands/deploy.md` → `/deploy` works in chat
- **Auto model routing** — `--auto-model` picks the right Atlas model per task
- **Safety layer** — blocks secret files, workspace escapes, and dangerous shell commands

## Agentic Mode

When you ask Atlas to do something that requires file access or shell commands, it will:

1. Inspect relevant files
2. Create a plan and describe what it will change
3. Ask for approval (in `ask` mode) before each tool call
4. Apply edits with a diff preview
5. Run tests or build if appropriate

Example:
```
you › Fix the TypeScript error in src/api.ts

atlas › Reading src/api.ts...
        ✓ read_file(path: src/api.ts)
        I can see the issue on line 43 — the return type is wrong. Here's the fix:

        [diff preview]

        ⚠  edit_file — approve? [Y]es / [N]o
```

## Tools

| Tool | What it does |
|---|---|
| `read_file` | Read a file (with optional line range) |
| `write_file` | Write a file (with backup) |
| `edit_file` | Replace a string in a file (with diff preview + backup) |
| `list_files` | List directory contents |
| `search_files` | Find files matching a glob pattern |
| `grep` | Search file contents with regex |
| `run_command` | Execute a shell command |
| `git_status` | Show git status |
| `git_diff` | Show git diff (staged or unstaged) |
| `git_commit_message` | Generate a commit message |
| `create_directory` | Create a directory |

## Permission Modes

Set with `/permissions <mode>` or in `.atlas/settings.json`:

| Mode | Behaviour |
|---|---|
| `readonly` | Only reads — no writes or shell commands |
| `ask` | Ask before every write, edit, or run _(default)_ |
| `auto` | Allow reads + edits silently; ask before shell commands |
| `danger` | Allow all tools (except explicit deny list) without asking |

## Slash Commands

```
/help          All commands
/init          Create .atlas/ and ATLAS.md
/status        Workspace + model + permissions summary
/doctor        Config health check
/clear         Clear conversation history
/compact       Summarise history to save context
/model [name]  Show or switch model
/permissions   Show or set permission mode
/read <path>   Read a file
/edit <path>   Start editing a file
/run <cmd>     Run a shell command
/test          Run npm/bun test
/build         Run npm/bun build
/git           git status
/diff          git diff
/review        Review staged changes
/commit-message  Generate a commit message
/memory        Show .atlas/memory.md
/plan <goal>   Make a step-by-step plan
/security      Security review of the codebase
/docs <path>   Generate documentation
/undo          Show recent tool actions
```

## Models

| Model | Best for |
|---|---|
| `atlas-code` | Code, debugging, architecture _(default)_ |
| `atlas-plan` | Planning, reasoning, long-form thinking |
| `atlas-secure` | Security review, threat modelling |
| `atlas-infra` | DevOps, Kubernetes, Terraform, CI/CD |
| `atlas-data` | SQL, analytics, data pipelines |
| `atlas-books` | Writing, documentation, summaries |
| `atlas-audit` | Compliance, audit logs, governance |

Use `--auto-model` to let Atlas pick the best model per message automatically.

## CLI Commands

```bash
atlas                        Interactive agentic chat
atlas init                   Set up .atlas/ workspace
atlas doctor                 Check config health
atlas diff                   Show git diff
atlas git                    Show git status
atlas review                 Show staged changes
atlas commit-message         Generate a commit message

atlas --model atlas-plan     Override model for session
atlas --auto-model           Auto-route model per message
atlas --no-agent             Plain chat only (no tools)
atlas --system "..."         Custom system prompt

atlas config                 Show config
atlas config set apiKey sk-…
atlas config set baseUrl https://atlas.alpheric.ai/v1
atlas config set model atlas-plan
```

## Workspace Structure

After `atlas init`:
```
your-project/
  ATLAS.md              # Project context — edit this
  .atlas/
    settings.json       # Permission config
    memory.md           # Session memory — add notes here
    commands/           # Custom slash commands (*.md)
    logs/
      audit.jsonl       # Tool execution log
    backups/            # File backups before edits
```

## Custom Commands

Create `.atlas/commands/deploy.md`:
```markdown
Deploy the application to $ARGUMENTS environment.
Check the deployment config, run tests, then generate the deploy command.
```

Now `/deploy staging` works in chat.

## Safety

Atlas will never:
- Read `.env`, `.pem`, private keys, or credential files without explicit approval
- Write files outside the workspace directory
- Run `rm -rf`, `sudo`, `curl | bash`, or other destructive commands without double confirmation
- Commit or push git changes automatically

All tool executions are logged to `.atlas/logs/audit.jsonl`. File backups are stored in `.atlas/backups/` before any edit.

## Install System-Wide

```bash
sudo ln -sf /path/to/atlas-cli/dist/atlas /usr/local/bin/atlas
```

Then run `atlas` from any project directory.

## Build from Source

```bash
cd atlas-cli
bun install
bash build.sh       # → dist/atlas (launcher), dist/atlas.js (bundle), dist/yoga.wasm
```

Requires [Bun](https://bun.sh) v1.0+.
