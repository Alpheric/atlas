#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# Atlas CLI Installer — Linux / macOS
#
# Usage:
#   curl -fsSL https://atlas.alpheric.ai/install.sh | bash
#
# What this does:
#   1. Installs Bun (if not already installed)
#   2. Downloads the Atlas CLI bundle to ~/.atlas-cli/
#   3. Creates the `atlas` command in /usr/local/bin (or ~/.local/bin)
# ─────────────────────────────────────────────────────────────────────────────
set -e

BASE_URL="https://atlas.alpheric.ai"
INSTALL_DIR="$HOME/.atlas-cli"

echo ""
# ANSI colour codes (green → cyan gradient, matches Atlas CLI banner)
G='\033[1;32m'; C='\033[0;36m'; DC='\033[0;36m'; DG='\033[2;37m'; R='\033[0m'
printf "${DG}  ╭────────────────────────────────────────────────╮${R}\n"
printf "${DG}  │                                                │${R}\n"
printf "${DG}  │  ${R}${G} ██████╗ ████████╗██╗      █████╗ ███████╗ ${R}${DG} │${R}\n"
printf "${DG}  │  ${R}${G}██╔══██╗╚══██╔══╝██║     ██╔══██╗██╔════╝ ${R}${DG} │${R}\n"
printf "${DG}  │  ${R}${C}███████║   ██║   ██║     ███████║███████╗  ${R}${DG}│${R}\n"
printf "${DG}  │  ${R}${C}██╔══██║   ██║   ██║     ██╔══██║╚════██║  ${R}${DG}│${R}\n"
printf "${DG}  │  ${R}${C}██║  ██║   ██║   ███████╗██║  ██║███████║  ${R}${DG}│${R}\n"
printf "${DG}  │  ${R}${C}╚═╝  ╚═╝   ╚═╝   ╚══════╝╚═╝  ╚═╝╚══════╝ ${R}${DG} │${R}\n"
printf "${DG}  │                                                │${R}\n"
printf "${DG}  │  ${R}${DC}          ✦  by Alpheric AI  ✦           ${R}${DG}  │${R}\n"
printf "${DG}  │   Agentic AI coding assistant for the terminal │${R}\n"
printf "${DG}  │                                                │${R}\n"
printf "${DG}  ╰────────────────────────────────────────────────╯${R}\n"
echo ""

# ── 1. Bun ────────────────────────────────────────────────────────────────────
if ! command -v bun >/dev/null 2>&1 && [ ! -f "$HOME/.bun/bin/bun" ]; then
  echo "→ Installing Bun..."
  curl -fsSL https://bun.sh/install | bash
  export PATH="$HOME/.bun/bin:$PATH"
else
  echo "✓ Bun already installed"
fi

BUN_BIN="bun"
if ! command -v bun >/dev/null 2>&1; then
  BUN_BIN="$HOME/.bun/bin/bun"
fi

# ── 2. Download Atlas CLI ─────────────────────────────────────────────────────
echo "→ Downloading Atlas CLI..."
mkdir -p "$INSTALL_DIR"

TMPFILE=$(mktemp /tmp/atlas-cli.XXXXXX.tar.gz)
curl -fsSL "$BASE_URL/downloads/atlas-cli.tar.gz" -o "$TMPFILE"

echo "→ Installing to $INSTALL_DIR..."
tar -xzf "$TMPFILE" -C "$INSTALL_DIR" --strip-components=1
rm -f "$TMPFILE"

chmod +x "$INSTALL_DIR/dist/atlas"

# ── 3. Create `atlas` command ─────────────────────────────────────────────────
# Preference order:
#   /opt/homebrew/bin  — Apple Silicon Macs (exists + writable via sudo)
#   /usr/local/bin     — Intel Macs / Linux with Homebrew (may need creating)
#   ~/.local/bin       — fallback (no sudo needed, user PATH only)
if [ -d /opt/homebrew/bin ] && sudo -n true 2>/dev/null; then
  LINK_DIR="/opt/homebrew/bin"
  USE_SUDO=1
elif [ -d /opt/homebrew/bin ]; then
  LINK_DIR="/opt/homebrew/bin"
  USE_SUDO=1
elif [ -w /usr/local/bin ]; then
  LINK_DIR="/usr/local/bin"
elif sudo -n true 2>/dev/null; then
  sudo mkdir -p /usr/local/bin
  LINK_DIR="/usr/local/bin"
  USE_SUDO=1
else
  LINK_DIR="$HOME/.local/bin"
  mkdir -p "$LINK_DIR"
fi

LINK="$LINK_DIR/atlas"
# Write a wrapper script instead of a symlink — symlinks break on macOS
# when the target is resolved via dirname, because readlink behaviour varies.
WRAPPER="#!/bin/sh
for BIN in bun \"\$HOME/.bun/bin/bun\" /opt/homebrew/bin/bun /usr/local/bin/bun; do
  if command -v \"\$BIN\" >/dev/null 2>&1; then
    exec \"\$BIN\" run \"$INSTALL_DIR/dist/atlas.js\" \"\$@\"
  fi
done
echo 'Error: bun not found. Install from https://bun.sh' >&2
exit 1"

if [ -n "$USE_SUDO" ]; then
  echo "$WRAPPER" | sudo tee "$LINK" > /dev/null
  sudo chmod +x "$LINK"
else
  printf '%s\n' "$WRAPPER" > "$LINK"
  chmod +x "$LINK"
fi

# ── 4. PATH check + auto-add if using ~/.local/bin ───────────────────────────
case ":$PATH:" in
  *":$LINK_DIR:"*) ;;
  *)
    # Auto-add to shell profile for ~/.local/bin (no sudo path)
    if [ "$LINK_DIR" = "$HOME/.local/bin" ]; then
      PROFILE="$HOME/.zshrc"
      [ -n "$BASH_VERSION" ] && PROFILE="$HOME/.bashrc"
      EXPORT_LINE="export PATH=\"\$HOME/.local/bin:\$PATH\""
      if ! grep -qF '.local/bin' "$PROFILE" 2>/dev/null; then
        echo "" >> "$PROFILE"
        echo "# Atlas CLI" >> "$PROFILE"
        echo "$EXPORT_LINE" >> "$PROFILE"
        echo "  ✓ Added ~/.local/bin to $PROFILE"
        echo "  ⚠  Run: source $PROFILE  (or open a new terminal)"
      fi
    else
      echo ""
      echo "  ⚠  Add to your shell profile if 'atlas' is not found:"
      echo "     export PATH=\"$LINK_DIR:\$PATH\""
    fi
    ;;
esac

echo ""
echo "  ✓ Atlas CLI installed!"
echo ""
echo "  Next steps:"
echo "    atlas config set apiKey  <your-key>"
echo "    atlas config set baseUrl https://atlas.alpheric.ai/v1"
echo "    cd your-project && atlas"
echo ""
