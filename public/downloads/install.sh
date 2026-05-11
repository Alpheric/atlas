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

# ── 5. Configure API key + baseUrl ────────────────────────────────────────────
# When installed via `curl | bash`, stdin is the pipe — read from /dev/tty
# so the prompts actually work.
CONFIG_DIR="$HOME/.config/atlas-cli"
CONFIG_FILE="$CONFIG_DIR/config.json"
mkdir -p "$CONFIG_DIR"

EXISTING_KEY=""
if [ -f "$CONFIG_FILE" ]; then
  # Extract existing apiKey if any (best-effort; no jq dependency)
  EXISTING_KEY=$(sed -n 's/.*"apiKey"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$CONFIG_FILE" | head -n1)
fi

# Skip prompt if env var already provides a key, or if config already has one.
ENV_KEY="${ATLAS_API_KEY:-${ALPHERIC_API_KEY:-}}"

if [ -n "$ENV_KEY" ]; then
  printf "  ${G}✓${R} Using API key from environment (ATLAS_API_KEY)\n"
elif [ -n "$EXISTING_KEY" ]; then
  printf "  ${G}✓${R} API key already configured at %s\n" "$CONFIG_FILE"
elif [ ! -t 0 ] && [ ! -e /dev/tty ]; then
  echo "  ⚠  No TTY available — skipping API key prompt."
  echo "     Run later:  atlas config set apiKey <your-key>"
else
  echo ""
  printf "  Enter your Atlas API key (starts with sk-atlas-) — or press Enter to skip:\n"
  printf "  ${C}> ${R}"
  if [ -e /dev/tty ]; then
    read -r USER_KEY < /dev/tty || USER_KEY=""
  else
    read -r USER_KEY || USER_KEY=""
  fi

  if [ -n "$USER_KEY" ]; then
    # Escape backslashes and double quotes for JSON safety
    ESC_KEY=$(printf '%s' "$USER_KEY" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
    cat > "$CONFIG_FILE" <<EOF
{
  "apiKey": "$ESC_KEY",
  "baseUrl": "https://atlas.alpheric.ai/v1",
  "model": "atlas-code",
  "stream": true
}
EOF
    chmod 600 "$CONFIG_FILE"
    printf "  ${G}✓${R} API key saved to %s\n" "$CONFIG_FILE"
  else
    echo "  ℹ  Skipped. Configure later with: atlas config set apiKey <your-key>"
  fi
fi

echo ""
echo "  Next steps:"
echo "    cd your-project && atlas"
echo ""
echo "  Don't have a key? Get one at https://atlas.alpheric.ai"
echo ""
