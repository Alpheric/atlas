/**
 * Lightweight markdown → plain-text renderer for terminal output.
 * Uses marked + marked-terminal when available; falls back to simple strip.
 */

let _render: ((md: string) => string) | null = null;

function getRenderer(): (md: string) => string {
  if (_render) return _render;

  try {
    // Dynamic require so Bun bundler doesn't fail if import errors
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { marked } = require("marked");
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { markedTerminal } = require("marked-terminal");
    marked.use(markedTerminal());
    _render = (md: string) => marked(md) as string;
  } catch {
    // Fallback: strip markdown syntax
    _render = (md: string) =>
      md
        .replace(/^#{1,6}\s+/gm, "")
        .replace(/\*\*(.+?)\*\*/g, "$1")
        .replace(/\*(.+?)\*/g, "$1")
        .replace(/`{3}[\s\S]*?`{3}/g, (m) => m)
        .replace(/`(.+?)`/g, "$1")
        .replace(/\[(.+?)\]\(.+?\)/g, "$1");
  }

  return _render!;
}

export function renderMarkdown(text: string): string {
  try {
    return getRenderer()(text).trimEnd();
  } catch {
    return text;
  }
}
