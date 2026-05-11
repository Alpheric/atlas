#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Atlas CLI — build + publish
#
# Usage:
#   ./publish.sh
#
# What it does:
#   1. Builds the JS bundle (bun build)
#   2. Creates atlas-cli.tar.gz
#   3. Bumps public/downloads/version.txt  (timestamp = YYYYMMDDHHMM)
#   4. Copies tarball + version.txt to public/downloads/
#
# Users with Atlas CLI installed get the update silently on next launch.
# ─────────────────────────────────────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST="$SCRIPT_DIR/dist"
DOWNLOADS="$REPO_ROOT/public/downloads"

# ── 1. Build ──────────────────────────────────────────────────────────────────
echo "→ Building Atlas CLI…"
cd "$SCRIPT_DIR"
bash build.sh

# ── 2. Package ───────────────────────────────────────────────────────────────
echo "→ Packaging…"
TARBALL="$DOWNLOADS/atlas-cli.tar.gz"
cd "$SCRIPT_DIR"
tar -czf "$TARBALL" \
  --transform 's|^dist|atlas-cli/dist|' \
  dist/atlas.js \
  dist/atlas \
  dist/yoga.wasm

# ── 3. Copy individual files (used by direct-download updater) ───────────────
echo "→ Copying individual files for direct-download updater…"
cp "$DIST/atlas.js"  "$DOWNLOADS/atlas.js"
cp "$DIST/yoga.wasm" "$DOWNLOADS/yoga.wasm"

# ── 4. Bump version ───────────────────────────────────────────────────────────
VERSION=$(date +%Y%m%d%H%M)
echo "$VERSION" > "$DOWNLOADS/version.txt"

echo ""
echo "  ✓ Published atlas-cli v$VERSION"
echo "  ✓ Tarball  → $TARBALL  ($(du -sh "$TARBALL" | cut -f1))"
echo "  ✓ atlas.js → $DOWNLOADS/atlas.js"
echo "  ✓ Version  → $DOWNLOADS/version.txt"
echo ""
echo "  Users will receive this update silently on next 'atlas' launch."
echo ""
