#!/bin/bash
set -e

DIST="$(dirname "$0")/dist"
mkdir -p "$DIST"

echo "Building atlas.js bundle…"
~/.bun/bin/bun build src/index.tsx \
  --outfile "$DIST/atlas.js" \
  --target bun \
  --minify

echo "Copying yoga.wasm…"
cp node_modules/yoga-wasm-web/dist/yoga.wasm "$DIST/yoga.wasm"

echo "Writing launcher…"
cat > "$DIST/atlas" << 'LAUNCHER'
#!/bin/sh
# Atlas Code CLI launcher — requires bun (https://bun.sh)
#
# Resolves symlinks so DIR always points to the real dist/ folder,
# even when `atlas` is symlinked into /usr/local/bin or ~/.local/bin.
SELF="$0"
while [ -L "$SELF" ]; do
  TARGET="$(readlink "$SELF")"
  case "$TARGET" in
    /*) SELF="$TARGET" ;;
    *)  SELF="$(dirname "$SELF")/$TARGET" ;;
  esac
done
DIR="$(cd "$(dirname "$SELF")" && pwd)"

for BUN in bun "$HOME/.bun/bin/bun" /opt/homebrew/bin/bun /usr/local/bin/bun; do
  if command -v "$BUN" >/dev/null 2>&1; then
    exec "$BUN" run "$DIR/atlas.js" "$@"
  fi
done
echo "Error: bun is not installed. Install from https://bun.sh" >&2
exit 1
LAUNCHER
chmod +x "$DIST/atlas"

echo "Done → $DIST/atlas"
ls -lh "$DIST"
