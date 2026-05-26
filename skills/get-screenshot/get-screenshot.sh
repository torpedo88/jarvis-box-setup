#!/usr/bin/env bash
#
# get-screenshot.sh — pull macOS screenshots from the Mac into Jarvis over SSH.
#
# Subcommands:
#   latest          Pull the single most recent screenshot (default).
#   list [N]        Show the N most recent screenshots (default 10) and
#                   record a manifest for `pull`.
#   pull <idx...>   Pull screenshots by index from the last `list`.
#   doctor          Run preflight checks.
#
# Config via env:
#   SCREENSHOT_MAC_HOST     ssh host alias for the Mac (default: mac)
#   SCREENSHOT_LANDING_DIR  where files land on Jarvis (default: ~/screenshots)
#
set -euo pipefail

MAC_HOST="${SCREENSHOT_MAC_HOST:-mac}"
LANDING_DIR="${SCREENSHOT_LANDING_DIR:-$HOME/screenshots}"
MANIFEST="$LANDING_DIR/.manifest"
LIST_DEFAULT=10
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8)

mkdir -p "$LANDING_DIR"

die() { echo "get-screenshot: $*" >&2; exit 1; }

require_mac() {
  ssh "${SSH_OPTS[@]}" "$MAC_HOST" true 2>/dev/null \
    || die "can't reach Mac via '$MAC_HOST' — is it awake, on Tailscale, and is Remote Login on? (try: get-screenshot doctor)"
}

# Echo the Mac's screenshot directory (configured location, else ~/Desktop).
remote_dir() {
  ssh "${SSH_OPTS[@]}" "$MAC_HOST" bash -s <<'REMOTE'
loc=$(defaults read com.apple.screencapture location 2>/dev/null || true)
case "$loc" in
  "~"*) loc="$HOME${loc#\~}" ;;
esac
if [ -n "$loc" ] && [ -d "$loc" ]; then
  printf '%s\n' "$loc"
else
  printf '%s\n' "$HOME/Desktop"
fi
REMOTE
}

# Echo newest-first list as: epoch<TAB>size<TAB>name. Args: <dir> <count>
fetch_list() {
  local dir_b64; dir_b64=$(printf '%s' "$1" | base64 | tr -d '\n')
  ssh "${SSH_OPTS[@]}" "$MAC_HOST" bash -s -- "$dir_b64" "$2" <<'REMOTE'
dir=$(printf '%s' "$1" | base64 -d); n="$2"
cd "$dir" 2>/dev/null || exit 0
shopt -s nullglob nocaseglob
set -- *.png *.jpg *.jpeg *.gif *.webp *.heic
[ "$#" -gt 0 ] || exit 0
ls -t -- "$@" 2>/dev/null | head -n "$n" | while IFS= read -r f; do
  stat -f '%m %z %N' -- "$f"
done
REMOTE
}

# Stream one remote file to a local dest (binary- and space-safe).
# Args: <remote-abs-path> <local-dest>
pull_file() {
  local remotepath="$1" dest="$2" rp_b64
  rp_b64=$(printf '%s' "$remotepath" | base64 | tr -d '\n')
  if ssh "${SSH_OPTS[@]}" "$MAC_HOST" bash -s -- "$rp_b64" > "$dest" <<'REMOTE'
f=$(printf '%s' "$1" | base64 -d)
[ -f "$f" ] || { echo "remote file missing: $f" >&2; exit 5; }
cat -- "$f"
REMOTE
  then
    [ -s "$dest" ] || { rm -f "$dest"; die "pulled empty file: $remotepath"; }
  else
    rm -f "$dest"; die "failed to pull: $remotepath"
  fi
}

sanitize() { printf '%s' "$1" | tr ' ' '_'; }

cmd_latest() {
  require_mac
  local dir line name dest epoch size
  dir=$(remote_dir)
  line=$(fetch_list "$dir" 1)
  [ -n "$line" ] || die "no images found in $dir on '$MAC_HOST'"
  read -r epoch size name <<< "$line"
  dest="$LANDING_DIR/$(sanitize "$name")"
  pull_file "$dir/$name" "$dest"
  echo "$dest"
}

cmd_list() {
  require_mac
  local n="${1:-$LIST_DEFAULT}" dir lines i=0 epoch size name t s
  dir=$(remote_dir)
  lines=$(fetch_list "$dir" "$n")
  [ -n "$lines" ] || die "no images found in $dir on '$MAC_HOST'"
  : > "$MANIFEST"
  while read -r epoch size name; do
    [ -n "$name" ] || continue
    i=$((i+1))
    printf '%s\t%s/%s\n' "$i" "$dir" "$name" >> "$MANIFEST"
    t=$(date -d "@$epoch" '+%Y-%m-%d %H:%M' 2>/dev/null || printf '%s' "$epoch")
    s=$(numfmt --to=iec "$size" 2>/dev/null || printf '%sB' "$size")
    printf '%2d) %s  %7s  %s\n' "$i" "$t" "$s" "$name"
  done <<< "$lines"
  [ "$i" -gt 0 ] || die "no images found in $dir on '$MAC_HOST'"
}

cmd_pull() {
  [ "$#" -ge 1 ] || die "usage: get-screenshot pull <index...>"
  [ -f "$MANIFEST" ] || die "no manifest; run 'get-screenshot list' first"
  require_mac
  local idx remotepath name dest rc=0
  for idx in "$@"; do
    remotepath=$(awk -F'\t' -v i="$idx" '$1==i{print $2; exit}' "$MANIFEST")
    if [ -z "$remotepath" ]; then echo "get-screenshot: index $idx not in list" >&2; rc=1; continue; fi
    name=$(basename -- "$remotepath")
    dest="$LANDING_DIR/$(sanitize "$name")"
    pull_file "$remotepath" "$dest"
    echo "$dest"
  done
  return "$rc"
}

cmd_doctor() {
  local ok=0
  echo "MAC_HOST       = $MAC_HOST"
  echo "LANDING_DIR    = $LANDING_DIR"
  if ssh "${SSH_OPTS[@]}" "$MAC_HOST" true 2>/dev/null; then
    echo "ssh reachable  : yes"
    echo "screenshot dir : $(remote_dir)"
  else
    echo "ssh reachable  : NO  (Mac asleep/off, Tailscale down, or Remote Login disabled)"; ok=1
  fi
  if [ -d "$LANDING_DIR" ]; then echo "landing dir    : ok ($LANDING_DIR)"; else echo "landing dir    : MISSING"; ok=1; fi
  if [ -f /etc/tmpfiles.d/jarvis-screenshots.conf ]; then echo "cleanup config : installed"; else echo "cleanup config : NOT installed"; ok=1; fi
  return "$ok"
}

main() {
  local sub="${1:-latest}"
  case "$sub" in
    latest) shift || true; cmd_latest ;;
    list)   shift; cmd_list "$@" ;;
    pull)   shift; cmd_pull "$@" ;;
    doctor) shift || true; cmd_doctor ;;
    -h|--help|help) sed -n '2,17p' "$0" ;;
    *) die "unknown subcommand: $sub (use latest|list|pull|doctor)" ;;
  esac
}

main "$@"
