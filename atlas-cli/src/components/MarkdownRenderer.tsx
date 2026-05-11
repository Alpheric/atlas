/**
 * MarkdownRenderer — renders markdown as styled Ink terminal output.
 *
 * Supported:
 *   # ## ###        Headings (coloured, bold)
 *   **bold**         Bold text
 *   *italic*         Italic / dimmed text
 *   `inline code`    Highlighted inline code
 *   - / * / 1.      Bullet and numbered lists
 *   > blockquote     Left-bordered blockquote
 *   ```lang … ```   Syntax-coloured code blocks with language label + line numbers
 *   ---             Horizontal rule
 *
 * During streaming (streaming=true) a plain-text fast path is used to avoid
 * parsing partial markdown.  MarkdownRenderer is only the full renderer.
 */

import React from "react";
import { Box, Text } from "ink";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Block =
  | { type: "codeblock"; language: string; lines: string[] }
  | { type: "heading";   level: number;   text: string }
  | { type: "list";      ordered: boolean; items: string[] }
  | { type: "blockquote"; lines: string[] }
  | { type: "hr" }
  | { type: "paragraph"; lines: string[] };

interface InlineSpan {
  text:    string;
  bold?:   boolean;
  italic?: boolean;
  code?:   boolean;
}

// ---------------------------------------------------------------------------
// Inline parser  (**bold**, *italic*, `code`)
// ---------------------------------------------------------------------------

function parseInline(text: string): InlineSpan[] {
  const spans: InlineSpan[] = [];
  // ** must be matched before * to avoid greedy issues
  const re = /\*\*([^*\n]+)\*\*|\*([^*\n]+)\*|`([^`\n]+)`/g;
  let last = 0;
  let m: RegExpExecArray | null;

  while ((m = re.exec(text)) !== null) {
    if (m.index > last) spans.push({ text: text.slice(last, m.index) });
    if      (m[1] !== undefined) spans.push({ text: m[1], bold: true });
    else if (m[2] !== undefined) spans.push({ text: m[2], italic: true });
    else if (m[3] !== undefined) spans.push({ text: m[3], code: true });
    last = m.index + m[0].length;
  }
  if (last < text.length) spans.push({ text: text.slice(last) });
  return spans.length ? spans : [{ text }];
}

// ---------------------------------------------------------------------------
// Inline renderer
// ---------------------------------------------------------------------------

function InlineLine({ text, dim }: { text: string; dim?: boolean }) {
  const spans = parseInline(text);
  return (
    <Text dimColor={dim}>
      {spans.map((s, i) => {
        if (s.code) {
          return (
            <Text key={i} backgroundColor="gray" color="white">
              {` ${s.text} `}
            </Text>
          );
        }
        if (s.bold)   return <Text key={i} bold>{s.text}</Text>;
        if (s.italic) return <Text key={i} dimColor>{s.text}</Text>;
        return <Text key={i}>{s.text}</Text>;
      })}
    </Text>
  );
}

// ---------------------------------------------------------------------------
// Block parser
// ---------------------------------------------------------------------------

function parseBlocks(md: string): Block[] {
  const lines = md.split("\n");
  const blocks: Block[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // ── Code block ──────────────────────────────────────────────────────────
    if (line.trimStart().startsWith("```")) {
      const lang = line.trimStart().slice(3).trim().toLowerCase();
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trimStart().startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // skip closing ```
      blocks.push({ type: "codeblock", language: lang, lines: codeLines });
      continue;
    }

    // ── Heading ──────────────────────────────────────────────────────────────
    const hm = line.match(/^(#{1,3})\s+(.+)/);
    if (hm) {
      blocks.push({ type: "heading", level: hm[1].length, text: hm[2] });
      i++;
      continue;
    }

    // ── Horizontal rule ───────────────────────────────────────────────────────
    if (/^[-*_]{3,}\s*$/.test(line)) {
      blocks.push({ type: "hr" });
      i++;
      continue;
    }

    // ── List ──────────────────────────────────────────────────────────────────
    const lm = line.match(/^(\s*)([-*+]|\d+\.)\s+(.*)/);
    if (lm) {
      const ordered = /\d+\./.test(lm[2]);
      const items: string[] = [lm[3]];
      i++;
      while (i < lines.length) {
        const nm = lines[i].match(/^(\s*)([-*+]|\d+\.)\s+(.*)/);
        if (nm) { items.push(nm[3]); i++; }
        else break;
      }
      blocks.push({ type: "list", ordered, items });
      continue;
    }

    // ── Blockquote ────────────────────────────────────────────────────────────
    if (line.startsWith("> ")) {
      const qLines: string[] = [line.slice(2)];
      i++;
      while (i < lines.length && lines[i].startsWith("> ")) {
        qLines.push(lines[i].slice(2));
        i++;
      }
      blocks.push({ type: "blockquote", lines: qLines });
      continue;
    }

    // ── Skip blank lines ──────────────────────────────────────────────────────
    if (line.trim() === "") { i++; continue; }

    // ── Paragraph ─────────────────────────────────────────────────────────────
    const pLines: string[] = [line];
    i++;
    while (i < lines.length) {
      const next = lines[i];
      if (
        next.trim() === "" ||
        next.trimStart().startsWith("```") ||
        /^#{1,3}\s/.test(next) ||
        /^(\s*)([-*+]|\d+\.)\s/.test(next) ||
        next.startsWith("> ") ||
        /^[-*_]{3,}\s*$/.test(next)
      ) break;
      pLines.push(next);
      i++;
    }
    blocks.push({ type: "paragraph", lines: pLines });
  }

  return blocks;
}

// ---------------------------------------------------------------------------
// Syntax colouring for code blocks
// ---------------------------------------------------------------------------

const KEYWORDS_BY_LANG: Record<string, string[]> = {
  ts:   ["const","let","var","function","return","if","else","for","while","class","import","export","from","async","await","type","interface","extends","implements","new","typeof","instanceof","void","null","undefined","true","false","throw","try","catch","finally","switch","case","break","continue","default","enum","namespace","declare","readonly","public","private","protected","static","abstract"],
  js:   ["const","let","var","function","return","if","else","for","while","class","import","export","from","async","await","new","typeof","instanceof","void","null","undefined","true","false","throw","try","catch","finally","switch","case","break","continue","default"],
  py:   ["def","class","return","if","elif","else","for","while","import","from","as","with","try","except","finally","raise","pass","break","continue","None","True","False","and","or","not","in","is","lambda","yield","async","await","self","super"],
  go:   ["func","return","if","else","for","range","var","const","type","struct","interface","import","package","go","defer","select","case","break","continue","fallthrough","nil","true","false"],
  rs:   ["fn","let","mut","pub","use","mod","struct","enum","impl","trait","return","if","else","for","while","loop","match","break","continue","true","false","None","Some","Ok","Err","self","Self","super","crate"],
  sql:  ["SELECT","FROM","WHERE","JOIN","LEFT","RIGHT","INNER","OUTER","ON","GROUP","BY","ORDER","HAVING","INSERT","INTO","VALUES","UPDATE","SET","DELETE","CREATE","TABLE","INDEX","DROP","ALTER","ADD","COLUMN","PRIMARY","KEY","FOREIGN","REFERENCES","NOT","NULL","UNIQUE","DEFAULT","AS","DISTINCT","COUNT","SUM","AVG","MAX","MIN","AND","OR","IN","BETWEEN","LIKE","IS","EXISTS"],
};
KEYWORDS_BY_LANG["tsx"] = KEYWORDS_BY_LANG["ts"];
KEYWORDS_BY_LANG["jsx"] = KEYWORDS_BY_LANG["js"];
KEYWORDS_BY_LANG["python"] = KEYWORDS_BY_LANG["py"];
KEYWORDS_BY_LANG["rust"] = KEYWORDS_BY_LANG["rs"];

const COMMENT_PREFIXES: Record<string, string[]> = {
  ts: ["//", "/*", "*"], tsx: ["//", "/*", "*"],
  js: ["//", "/*", "*"], jsx: ["//", "/*", "*"],
  go: ["//", "/*", "*"], rs: ["//", "/*", "*"],
  py: ["#"], python: ["#"], ruby: ["#"], rb: ["#"],
  sh: ["#"], bash: ["#"], yaml: ["#"], yml: ["#"],
  sql: ["--"], lua: ["--"],
};

function colorCodeLine(line: string, lang: string): React.ReactElement {
  const trimmed = line.trimStart();

  // Comments
  const commentPfxs = COMMENT_PREFIXES[lang] ?? [];
  if (commentPfxs.some(p => trimmed.startsWith(p))) {
    return <Text dimColor>{line}</Text>;
  }

  // String literals (rough pass — covers most common cases)
  const stringRe = /("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)/;
  if (stringRe.test(line)) {
    const parts = line.split(stringRe);
    if (parts.length > 1) {
      return (
        <Text>
          {parts.map((p, pi) =>
            stringRe.test(p)
              ? <Text key={pi} color="yellow">{p}</Text>
              : <Text key={pi} color="white">{p}</Text>
          )}
        </Text>
      );
    }
  }

  // Keywords
  const kws = KEYWORDS_BY_LANG[lang];
  if (kws) {
    const kwPattern = new RegExp(`\\b(${kws.join("|")})\\b`);
    const parts = line.split(kwPattern);
    if (parts.length > 1) {
      return (
        <Text>
          {parts.map((p, pi) =>
            kwPattern.test(p) && kws.includes(p)
              ? <Text key={pi} color="cyan">{p}</Text>
              : <Text key={pi} color="white">{p}</Text>
          )}
        </Text>
      );
    }
  }

  return <Text color="white">{line}</Text>;
}

const LANG_LABEL: Record<string, string> = {
  ts: "TypeScript", tsx: "TSX", js: "JavaScript", jsx: "JSX",
  py: "Python", python: "Python", ruby: "Ruby", rb: "Ruby",
  rs: "Rust", rust: "Rust", go: "Go", java: "Java", kotlin: "Kotlin",
  sh: "Shell", bash: "Bash", zsh: "Zsh",
  sql: "SQL", json: "JSON", yaml: "YAML", yml: "YAML",
  css: "CSS", scss: "SCSS", html: "HTML", md: "Markdown",
  c: "C", cpp: "C++", cs: "C#", swift: "Swift", lua: "Lua",
  "": "code",
};

function CodeBlock({ language, lines }: { language: string; lines: string[] }) {
  const label = LANG_LABEL[language] ?? (language || "code");
  // Trim trailing blank lines
  const trimmed = [...lines];
  while (trimmed.length > 0 && trimmed[trimmed.length - 1].trim() === "") trimmed.pop();

  return (
    <Box flexDirection="column" marginTop={1} marginBottom={1}>
      {/* Language badge */}
      <Box>
        <Text backgroundColor="gray" color="white" bold>{` ${label} `}</Text>
      </Box>
      {/* Code body */}
      <Box flexDirection="column" borderStyle="single" borderColor="gray" paddingX={1}>
        {trimmed.map((line, i) => (
          <Box key={i}>
            <Text dimColor>{String(i + 1).padStart(3, " ")}  </Text>
            {colorCodeLine(line, language)}
          </Box>
        ))}
      </Box>
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Main exported component
// ---------------------------------------------------------------------------

export function MarkdownRenderer({ content }: { content: string }) {
  const blocks = parseBlocks(content);
  if (blocks.length === 0) return null;

  return (
    <Box flexDirection="column">
      {blocks.map((block, bi) => {
        switch (block.type) {
          // ── Headings ────────────────────────────────────────────────────────
          case "heading": {
            const [color, prefix] = (
              block.level === 1 ? ["greenBright", "━━ "] :
              block.level === 2 ? ["green",       "── "] :
                                  ["cyan",         "   "]
            ) as [string, string];
            return (
              <Box key={bi} marginTop={bi > 0 ? 1 : 0}>
                <Text color={color as Parameters<typeof Text>[0]["color"]} bold>
                  {prefix}{block.text}
                </Text>
              </Box>
            );
          }

          // ── Code block ──────────────────────────────────────────────────────
          case "codeblock":
            return <CodeBlock key={bi} language={block.language} lines={block.lines} />;

          // ── List ─────────────────────────────────────────────────────────────
          case "list":
            return (
              <Box key={bi} flexDirection="column" marginLeft={2} marginTop={1}>
                {block.items.map((item, ii) => (
                  <Box key={ii} gap={1}>
                    <Text color="cyan">
                      {block.ordered ? `${ii + 1}.` : "•"}
                    </Text>
                    <InlineLine text={item} />
                  </Box>
                ))}
              </Box>
            );

          // ── Blockquote ────────────────────────────────────────────────────────
          case "blockquote":
            return (
              <Box key={bi} flexDirection="column" marginLeft={1} marginTop={1}>
                {block.lines.map((line, li) => (
                  <Box key={li} gap={1}>
                    <Text color="yellow">│</Text>
                    <InlineLine text={line} dim />
                  </Box>
                ))}
              </Box>
            );

          // ── HR ────────────────────────────────────────────────────────────────
          case "hr":
            return (
              <Box key={bi} marginY={1}>
                <Text dimColor>{"─".repeat(48)}</Text>
              </Box>
            );

          // ── Paragraph ─────────────────────────────────────────────────────────
          case "paragraph":
            return (
              <Box key={bi} flexDirection="column" marginTop={bi > 0 ? 1 : 0}>
                {block.lines.map((line, li) => (
                  <InlineLine key={li} text={line} />
                ))}
              </Box>
            );
        }
      })}
    </Box>
  );
}
