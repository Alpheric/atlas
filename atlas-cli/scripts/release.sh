#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Atlas CLI — Release builder
# Builds the CLI bundle and packages it for distribution.
# Output: /var/www/dev/atlas/public/downloads/atlas-cli.tar.gz
#
# Usage:
#   bash atlas-cli/scripts/release.sh
# ─────────────────────────────────────────────────────────────────────────────
set -e

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CLI_DIR="$ROOT/atlas-cli"
DIST_DIR="$CLI_DIR/dist"
OUT_DIR="$ROOT/public/downloads"
VERSION=$(node -p "require('$CLI_DIR/package.json').version" 2>/dev/null || echo "1.0.0")

echo "▶ Building Atlas CLI v$VERSION..."
cd "$CLI_DIR"

# Install deps if needed
[ ! -d node_modules ] && ~/.bun/bin/bun install

# Build JS bundle + copy wasm
bash build.sh

# Package: dist/ + install scripts only (no source)
echo "▶ Packaging..."
mkdir -p "$OUT_DIR"

TMPDIR=$(mktemp -d)
mkdir -p "$TMPDIR/atlas-cli/dist"

cp "$DIST_DIR/atlas.js"    "$TMPDIR/atlas-cli/dist/"
cp "$DIST_DIR/yoga.wasm"   "$TMPDIR/atlas-cli/dist/"
cp "$DIST_DIR/atlas"       "$TMPDIR/atlas-cli/dist/"

# Minimal package.json for npm global install support
cat > "$TMPDIR/atlas-cli/package.json" << EOF
{
  "name": "@alpheric/atlas",
  "version": "$VERSION",
  "description": "Atlas Code CLI — Alpheric AI terminal assistant",
  "bin": { "atlas": "./dist/atlas" },
  "files": ["dist"]
}
EOF

tar -czf "$OUT_DIR/atlas-cli.tar.gz" -C "$TMPDIR" atlas-cli
rm -rf "$TMPDIR"

echo "✓ Released → $OUT_DIR/atlas-cli.tar.gz"
echo "  Install command:"
echo "  curl -fsSL https://atlas.alpheric.ai/install.sh | bash"
