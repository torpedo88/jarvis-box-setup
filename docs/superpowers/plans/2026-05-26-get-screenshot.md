# get-screenshot Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A personal Claude skill on Jarvis that lists/pulls macOS screenshots from the user's Mac over Tailscale SSH into `~/screenshots`, with 24h auto-cleanup.

**Architecture:** Reverse key-based SSH (Jarvis → Mac). One bash script with `latest`/`list`/`pull`/`doctor` subcommands, a `SKILL.md` that tells Claude how to drive it, and a `systemd-tmpfiles` entry for daily aging. File transfer uses `cat`-over-SSH (binary- and space-safe) to sidestep `scp` protocol/quoting differences across OpenSSH versions.

**Tech Stack:** bash, OpenSSH, Tailscale, systemd-tmpfiles. Spec: `docs/superpowers/specs/2026-05-26-screenshot-transfer-design.md`.

> **STATUS: IMPLEMENTED 2026-05-26.** The script as built differs from the task code below in three ways discovered during execution — see "Implementation notes" at the bottom. The authoritative source is `skills/get-screenshot/` in this repo (installed to `~/.claude/skills/get-screenshot/`).

> **Testing note:** This tool is SSH-dependent shell glue with no unit-test harness available on the box. Per-task verification uses real commands with expected output (the "test" steps) instead of a unit framework. Tasks 3–4 require the **Mac awake, on Tailscale, with at least one screenshot on the Desktop**.

## File Structure

- Create: `~/.claude/skills/get-screenshot/get-screenshot.sh` — the workhorse (all subcommands).
- Create: `~/.claude/skills/get-screenshot/SKILL.md` — tells Claude when/how to invoke it.
- Create: `/etc/tmpfiles.d/jarvis-screenshots.conf` — creates `~/screenshots` and ages files out after 1 day.
- Touches (config, one-time): `~/.ssh/jarvis_to_mac{,.pub}`, `~/.ssh/config`, Mac's `~/.ssh/authorized_keys`.

---

### Task 1: One-time SSH trust (Jarvis → Mac)

**Files:**
- Create: `~/.ssh/jarvis_to_mac`, `~/.ssh/jarvis_to_mac.pub`
- Modify: `~/.ssh/config`

- [ ] **Step 1: USER — enable Remote Login on the Mac**

On the Mac: System Settings → General → Sharing → toggle **Remote Login** ON. Note the Mac's Tailscale name.

- [ ] **Step 2: Find the Mac's Tailscale MagicDNS name from Jarvis**

Run: `tailscale status`
Expected: a line for the Mac, e.g. `100.x.y.z   abhisheks-mbp   amaharjan08@   macOS   -`. Record the hostname (`abhisheks-mbp`) — that becomes the MagicDNS name `abhisheks-mbp.<tailnet>.ts.net` (or just use the `100.x.y.z` IP).

- [ ] **Step 3: Generate a passphraseless key on Jarvis**

```bash
ssh-keygen -t ed25519 -f ~/.ssh/jarvis_to_mac -N "" -C "jarvis->mac get-screenshot"
```
Expected: creates `~/.ssh/jarvis_to_mac` and `.pub`. (Passphraseless is required for non-interactive transfer.)

- [ ] **Step 4: Write the `mac` host alias**

Append to `~/.ssh/config` (replace HostName with the value from Step 2), then fix perms:
```bash
cat >> ~/.ssh/config <<'EOF'

Host mac
    HostName abhisheks-mbp.<tailnet>.ts.net
    User abhishekmaharjan
    IdentityFile ~/.ssh/jarvis_to_mac
    IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
```

- [ ] **Step 5: Install the public key on the Mac**

Run: `ssh-copy-id -i ~/.ssh/jarvis_to_mac.pub mac`
Expected: prompts for the Mac account password **once**, then "Number of key(s) added: 1".

- [ ] **Step 6 (optional hardening): restrict the key to Jarvis's Tailscale IP**

Get Jarvis's tailnet IP: `tailscale ip -4` (e.g. `100.122.198.56`). On the Mac, prepend `from="100.122.198.56"` to the new line in `~/.ssh/authorized_keys`. Guide the user; skip if they decline.

- [ ] **Step 7: Verify passwordless SSH works**

Run: `ssh -o BatchMode=yes -o ConnectTimeout=8 mac 'echo ok && sw_vers -productName'`
Expected: prints `ok` and `macOS` with **no password prompt**. If it prompts or fails, stop and fix before continuing.

- [ ] **Step 8: Commit the plan progress note**

```bash
git add docs/superpowers/plans/2026-05-26-get-screenshot.md
git commit -m "chore: ssh trust established for get-screenshot (task 1)"
```

---

### Task 2: Create the skill script

**Files:**
- Create: `~/.claude/skills/get-screenshot/get-screenshot.sh`

- [ ] **Step 1: Create the script with full content**

```bash
mkdir -p ~/.claude/skills/get-screenshot
cat > ~/.claude/skills/get-screenshot/get-screenshot.sh <<'SCRIPT'
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
  ssh "${SSH_OPTS[@]}" "$MAC_HOST" bash -s -- "$1" "$2" <<'REMOTE'
dir="$1"; n="$2"
cd "$dir" 2>/dev/null || exit 0
ls -t -- Screenshot*.png 2>/dev/null | head -n "$n" | while IFS= read -r f; do
  stat -f '%m\t%z\t%N' -- "$f"
done
REMOTE
}

# Stream one remote file to a local dest (binary- and space-safe).
# Args: <remote-abs-path> <local-dest>
pull_file() {
  local remotepath="$1" dest="$2"
  if ssh "${SSH_OPTS[@]}" "$MAC_HOST" bash -s -- "$remotepath" > "$dest" <<'REMOTE'
f="$1"
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
  local dir line name dest
  dir=$(remote_dir)
  line=$(fetch_list "$dir" 1)
  [ -n "$line" ] || die "no screenshots found in $dir on '$MAC_HOST'"
  name=${line#*$'\t'}; name=${name#*$'\t'}
  dest="$LANDING_DIR/$(sanitize "$name")"
  pull_file "$dir/$name" "$dest"
  echo "$dest"
}

cmd_list() {
  require_mac
  local n="${1:-$LIST_DEFAULT}" dir lines i=0 epoch size name t s
  dir=$(remote_dir)
  lines=$(fetch_list "$dir" "$n")
  [ -n "$lines" ] || die "no screenshots found in $dir on '$MAC_HOST'"
  : > "$MANIFEST"
  while IFS=$'\t' read -r epoch size name; do
    [ -n "$name" ] || continue
    i=$((i+1))
    printf '%s\t%s/%s\n' "$i" "$dir" "$name" >> "$MANIFEST"
    t=$(date -d "@$epoch" '+%Y-%m-%d %H:%M' 2>/dev/null || printf '%s' "$epoch")
    s=$(numfmt --to=iec "$size" 2>/dev/null || printf '%sB' "$size")
    printf '%2d) %s  %7s  %s\n' "$i" "$t" "$s" "$name"
  done <<< "$lines"
  [ "$i" -gt 0 ] || die "no screenshots found in $dir on '$MAC_HOST'"
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
SCRIPT
chmod +x ~/.claude/skills/get-screenshot/get-screenshot.sh
```

- [ ] **Step 2: Verify the script parses (syntax check)**

Run: `bash -n ~/.claude/skills/get-screenshot/get-screenshot.sh && echo SYNTAX_OK`
Expected: `SYNTAX_OK` with no errors.

- [ ] **Step 3: Verify the sanitize helper logic in isolation**

Run:
```bash
bash -c 'tr " " "_" <<< "Screenshot 2026-05-26 at 10.49.41 AM.png"'
```
Expected: `Screenshot_2026-05-26_at_10.49.41_AM.png`

- [ ] **Step 4: Verify `doctor` runs and reports SSH reachable**

Run: `~/.claude/skills/get-screenshot/get-screenshot.sh doctor`
Expected: `ssh reachable  : yes`, a `screenshot dir :` line, `landing dir : ok`. (`cleanup config : NOT installed` is expected until Task 5 — that's fine here.)

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-05-26-get-screenshot.md
git commit -m "feat: add get-screenshot.sh script (task 2)"
```
(The script lives under `~/.claude`, outside the repo, so only the plan checkbox state is committed.)

---

### Task 3: Verify `latest` end-to-end

**Files:** none (verification of Task 2's script).
**Precondition:** take a fresh screenshot on the Mac (`Cmd+Shift+4`) so there is at least one `Screenshot*.png` on the Desktop.

- [ ] **Step 1: Pull the latest screenshot**

Run: `~/.claude/skills/get-screenshot/get-screenshot.sh latest`
Expected: one line, an absolute path like `/home/alienware/screenshots/Screenshot_2026-05-26_at_10.49.41_AM.png`.

- [ ] **Step 2: Verify the file landed, is non-empty, and is a PNG**

Run: `f=$(~/.claude/skills/get-screenshot/get-screenshot.sh latest); ls -l "$f"; file "$f"`
Expected: a non-zero size and `PNG image data` in the `file` output.

- [ ] **Step 3: Verify the no-screenshots error path is friendly**

Run: `SCREENSHOT_MAC_HOST=mac bash -c '~/.claude/skills/get-screenshot/get-screenshot.sh list 0 2>&1 || true'`
Expected: a clean `get-screenshot: no screenshots found ...` message (not a raw traceback).

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/plans/2026-05-26-get-screenshot.md
git commit -m "test: verify get-screenshot latest end-to-end (task 3)"
```

---

### Task 4: Verify `list` + `pull` end-to-end

**Files:** none (verification).
**Precondition:** at least two screenshots on the Mac Desktop for a meaningful list.

- [ ] **Step 1: List recent screenshots**

Run: `~/.claude/skills/get-screenshot/get-screenshot.sh list`
Expected: a numbered table, newest first, e.g.:
```
 1) 2026-05-26 10:49   1.2M  Screenshot 2026-05-26 at 10.49.41 AM.png
 2) 2026-05-26 09:12   880K  Screenshot 2026-05-26 at 9.12.03 AM.png
```

- [ ] **Step 2: Verify the manifest was written**

Run: `cat ~/screenshots/.manifest`
Expected: tab-separated `index<TAB>/full/remote/path` lines matching the table order.

- [ ] **Step 3: Pull one by index**

Run: `~/.claude/skills/get-screenshot/get-screenshot.sh pull 1`
Expected: one absolute path printed; `ls -l` that path shows a non-empty PNG.

- [ ] **Step 4: Pull multiple by index**

Run: `~/.claude/skills/get-screenshot/get-screenshot.sh pull 1 2`
Expected: two absolute paths printed, both files present.

- [ ] **Step 5: Verify bad index is handled**

Run: `~/.claude/skills/get-screenshot/get-screenshot.sh pull 99 2>&1 || true`
Expected: `get-screenshot: index 99 not in list` and non-zero exit, no crash.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/plans/2026-05-26-get-screenshot.md
git commit -m "test: verify get-screenshot list+pull end-to-end (task 4)"
```

---

### Task 5: Install the daily cleanup

**Files:**
- Create: `/etc/tmpfiles.d/jarvis-screenshots.conf`

- [ ] **Step 1: Write the tmpfiles config**

```bash
echo 'd /home/alienware/screenshots 0755 alienware alienware 1d' | sudo tee /etc/tmpfiles.d/jarvis-screenshots.conf
```
Expected: the line is echoed back. (`d` ensures the dir exists; the `1d` age field tells `systemd-tmpfiles --clean` to delete files older than 1 day.)

- [ ] **Step 2: Validate the config and create the dir**

Run: `sudo systemd-tmpfiles --create /etc/tmpfiles.d/jarvis-screenshots.conf && echo CREATE_OK`
Expected: `CREATE_OK`, no parse errors.

- [ ] **Step 3: Verify aging removes an old file but keeps a fresh one**

```bash
touch ~/screenshots/keep_me.png
touch -d '2 days ago' ~/screenshots/old_one.png
sudo systemd-tmpfiles --clean /etc/tmpfiles.d/jarvis-screenshots.conf
ls ~/screenshots
```
Expected: `old_one.png` is gone, `keep_me.png` remains. (Clean up `keep_me.png` afterward.)

- [ ] **Step 4: Confirm the system clean timer is active**

Run: `systemctl is-active systemd-tmpfiles-clean.timer`
Expected: `active` (this is the already-enabled daily timer that will run our config).

- [ ] **Step 5: Verify `doctor` now reports the cleanup installed**

Run: `~/.claude/skills/get-screenshot/get-screenshot.sh doctor`
Expected: `cleanup config : installed`.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/plans/2026-05-26-get-screenshot.md
git commit -m "feat: install 24h tmpfiles cleanup for screenshots (task 5)"
```

---

### Task 6: Write SKILL.md

**Files:**
- Create: `~/.claude/skills/get-screenshot/SKILL.md`

- [ ] **Step 1: Create SKILL.md**

```bash
cat > ~/.claude/skills/get-screenshot/SKILL.md <<'MD'
---
name: get-screenshot
description: Use when the user wants to view, grab, pull, see, or look at a screenshot (or recent screenshots) from their Mac while Claude runs on the headless Jarvis box. Fetches macOS screenshots over SSH into ~/screenshots so Claude can read them as images. Also use when the user pastes a /Users/... Mac path that does not exist on this Linux box.
---

# get-screenshot

Pulls macOS screenshots from the user's Mac onto Jarvis over SSH so Claude can read them as images. Claude runs on Jarvis (headless Linux) and cannot see the Mac's clipboard, GUI, or `/Users/...` paths — files must be fetched first.

Script: `~/.claude/skills/get-screenshot/get-screenshot.sh`

## Grab the latest (default, fastest)
Run:

    ~/.claude/skills/get-screenshot/get-screenshot.sh latest

It prints one absolute path under `~/screenshots`. Read that path as an image.

## Let the user pick from a list
1. Run:

       ~/.claude/skills/get-screenshot/get-screenshot.sh list

   It prints a numbered table (index, time, size, name), newest first.
2. Show the table and ask which index/indices they want.
3. Run (one or several indices):

       ~/.claude/skills/get-screenshot/get-screenshot.sh pull 1 3

   It prints one absolute path per pulled file. Read them as images.

## Troubleshooting
If a fetch fails, run:

    ~/.claude/skills/get-screenshot/get-screenshot.sh doctor

and relay the failed check. Most common cause: the Mac is asleep or Remote Login is off.

## Notes
- Files land in `~/screenshots` and are auto-deleted after 24h (systemd-tmpfiles).
- Never reference Mac-side `/Users/...` paths directly — fetch with this skill instead.
MD
```

- [ ] **Step 2: Verify SKILL.md frontmatter is valid**

Run: `head -4 ~/.claude/skills/get-screenshot/SKILL.md`
Expected: starts with `---`, a `name: get-screenshot` line, and a `description:` line.

- [ ] **Step 3: Verify the skill is discoverable**

In a fresh Claude Code session, confirm `get-screenshot` appears in the available skills list (or run `/get-screenshot`). Expected: the skill loads and shows the usage above.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/plans/2026-05-26-get-screenshot.md
git commit -m "feat: add get-screenshot SKILL.md (task 6)"
```

---

### Task 7: Full end-to-end acceptance

**Files:** none (final verification).

- [ ] **Step 1: doctor is all-green**

Run: `~/.claude/skills/get-screenshot/get-screenshot.sh doctor; echo "exit=$?"`
Expected: `ssh reachable : yes`, `landing dir : ok`, `cleanup config : installed`, `exit=0`.

- [ ] **Step 2: Real-world flow — latest**

Take a screenshot on the Mac, then ask Claude in-session: "grab my latest screenshot." Expected: Claude invokes the skill, pulls the file, and reads/describes the image.

- [ ] **Step 3: Real-world flow — list + pick**

Ask Claude: "show me my recent screenshots and grab the 2nd one." Expected: Claude shows the table, you confirm, it pulls index 2 and reads it.

- [ ] **Step 4: Negative path — Mac unreachable**

Temporarily verify graceful failure: `SCREENSHOT_MAC_HOST=doesnotexist ~/.claude/skills/get-screenshot/get-screenshot.sh latest 2>&1 || true`
Expected: `get-screenshot: can't reach Mac via 'doesnotexist' ...` friendly message, non-zero exit.

- [ ] **Step 5: Final commit**

```bash
git add docs/superpowers/plans/2026-05-26-get-screenshot.md
git commit -m "test: get-screenshot full end-to-end acceptance (task 7)"
```

---

## Implementation notes (post-build, 2026-05-26)

Three issues surfaced during execution that changed the script from the task code above. The repo copy in `skills/get-screenshot/` is authoritative.

1. **macOS TCC blocks SSH from `~/Desktop`.** `ssh mac 'ls ~/Desktop'` returns `Operation not permitted` — modern macOS denies the SSH daemon access to Desktop/Documents/Downloads without Full Disk Access. Resolution (user chose least-privilege): use a non-protected drop folder `~/Screenshots` instead. The macOS screenshot save-location set over SSH (`defaults write com.apple.screencapture location` + `killall SystemUIServer`) does **not** stick in the live GUI session — set it via the on-screen `Cmd+Shift+5 → Options → Save to` UI, or just drag any image into `~/Screenshots`.

2. **SSH does not preserve argument boundaries.** `ssh mac bash -s -- "$path"` joins argv with spaces and the remote shell re-splits, so paths with spaces (every macOS screenshot) broke. Fix: base64-encode the path locally and `base64 -d` on the Mac (base64 has no characters the remote shell will re-split).

3. **BSD `stat -f` emits literal `\t`, not tabs.** The `%m\t%z\t%N` format produced the literal string `\t`, breaking tab-based field parsing. Fix: use a space delimiter (`%m %z %N`) and parse with `read -r epoch size name` (epoch/size are numeric; name is the remainder, spaces preserved).

**Scope change:** matcher broadened from `Screenshot*.png` to any recent image (`*.png *.jpg *.jpeg *.gif *.webp *.heic`, via `shopt -s nullglob nocaseglob`). `~/Screenshots` is now a general image drop folder, which is more robust than depending on the flaky macOS screenshot-location redirect. SKILL.md and the spec wording reflect this.

**Verified:** end-to-end pull + Claude reads the image; `list`/`pull`/bad-index; tmpfiles aging proven with a genuinely-aged file (the naive `touch -d '2 days ago'` test gives a false negative because `touch` can't backdate ctime, and tmpfiles ages on the most-recent of atime/btime/ctime/mtime); unreachable-Mac friendly error.
