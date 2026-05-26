# Design: `get-screenshot` — pull Mac screenshots into Jarvis

**Date:** 2026-05-26
**Status:** Approved (pending spec review)

## Problem

Claude Code runs on the headless Jarvis box (Linux). Screenshots are taken on the
user's Mac and live in the Mac's clipboard / `~/Desktop`. Clipboard paste and
drag-and-drop cannot work because the remote process has no access to the Mac's
pasteboard or GUI. The only way to give Claude an image is to land the file on
Jarvis and reference its path.

Goal: a one-command workflow, invoked from inside the Claude session on Jarvis,
that lists and/or grabs Mac screenshots, copies the chosen image(s) to Jarvis,
and auto-cleans old copies daily.

## Architecture

A personal Claude skill on Jarvis (`~/.claude/skills/get-screenshot/`) that reaches
**into the Mac over Tailscale via key-based SSH** (reverse of the usual
Mac→Jarvis direction). It pulls screenshots into a landing directory and relies on
`systemd-tmpfiles` to age them out after 24 hours.

Both machines are already on Tailscale. The Mac must have **Remote Login (SSH
server)** enabled for Jarvis to reach it.

### Authentication: dedicated SSH key

- ed25519 keypair generated on Jarvis at `~/.ssh/jarvis_to_mac`, **passphraseless**
  (required so `scp` runs non-interactively from the skill).
- Public half added to the Mac's `~/.ssh/authorized_keys`.
- Hardening: restrict the key entry with `from="<jarvis-tailscale-ip>"` so it is
  only usable from Jarvis.
- Chosen over Tailscale SSH (`--ssh`) because Tailscale SSH on macOS is
  flaky/limited depending on the installed Tailscale build; key-based SSH works
  with plain Remote Login regardless.

### SSH config

Jarvis `~/.ssh/config` gets a `mac` host alias:

```
Host mac
    HostName <mac-magicdns-name>   # e.g. abhisheks-mac.<tailnet>.ts.net
    User abhishekmaharjan
    IdentityFile ~/.ssh/jarvis_to_mac
```

All scripts refer only to `mac`.

## One-time setup (guided, run once)

1. **Mac:** System Settings → General → Sharing → enable **Remote Login**.
2. **Jarvis:** `ssh-keygen -t ed25519 -f ~/.ssh/jarvis_to_mac -N ""`.
3. **Jarvis → Mac:** `ssh-copy-id -i ~/.ssh/jarvis_to_mac.pub mac` (one password
   prompt). Then optionally edit the Mac's `authorized_keys` to prepend
   `from="<jarvis-tailscale-ip>"` to that key line.
4. **Jarvis:** write the `~/.ssh/config` `mac` alias above (Mac's MagicDNS name
   discovered via `tailscale status`).
5. Install the tmpfiles cleanup config (see Cleanup).

## Components

### `~/.claude/skills/get-screenshot/SKILL.md`
Instructs Claude when to invoke the script and how to interpret each subcommand's
output (notably: after a pull, read the printed path as an image; after `list`,
show the numbered table and wait for the user's index selection).

### `~/.claude/skills/get-screenshot/get-screenshot.sh`
The workhorse. Subcommands:

- **`latest`** (default): find newest `Screenshot*.png` in the Mac screenshot dir,
  `scp` it to the landing dir, print the absolute local path.
- **`list [N]`**: list the N most recent (default 10) as a numbered table
  (index, timestamp, size); also write a manifest file mapping index → remote
  path. Manifest decouples listing from pulling so a screenshot taken between the
  two steps cannot shift the selection.
- **`pull <idx…>`**: read the manifest, `scp` the given indices (one or several),
  print each resulting local path.
- **`doctor`**: preflight checks — SSH reachability to `mac`, screenshot-dir
  detection, landing dir existence, tmpfiles config installed. For troubleshooting.

### `/etc/tmpfiles.d/jarvis-screenshots.conf`
One line that creates the landing dir and ages out old files:

```
d /home/alienware/screenshots 0755 alienware alienware 1d
```

The `1d` age field deletes files older than 1 day. Executed by the system's
already-enabled daily `systemd-tmpfiles-clean.timer` — no custom timer/service to
maintain. Consistent with the box's systemd-first convention.

## Behavior details

- **Screenshot directory** auto-detected on the Mac via
  `ssh mac 'defaults read com.apple.screencapture location 2>/dev/null'`, falling
  back to `~/Desktop` (the macOS default; the `defaults` key only exists if the
  user customized the location).
- **File pattern:** `Screenshot*.png` (covers the default macOS naming, e.g.
  `Screenshot 2026-05-26 at 10.49.41 AM.png`).
- **Landing dir:** `~/screenshots/` on Jarvis (`/home/alienware/screenshots`).
- **Filename sanitization:** spaces → underscores on landing so paths are easy to
  type and quote, e.g. `Screenshot_2026-05-26_at_10.49.41_AM.png`.
- After a pull, the script prints the absolute path; Claude reads it as an image.

## Data flow

- *"grab my latest screenshot"* → `get-screenshot.sh latest` → `ssh`+`scp` →
  prints `/home/alienware/screenshots/…` → Claude reads it.
- *"show my recent screenshots"* → `get-screenshot.sh list` → Claude shows the
  numbered table → user replies e.g. `1,3` → `get-screenshot.sh pull 1 3` →
  Claude reads both.

## Error handling

- **Mac unreachable** (asleep/off, Tailscale down, Remote Login off): non-zero
  exit with a plain message naming the likely cause; no partial files left behind.
  Known limitation: a sleeping Mac will not answer SSH — `doctor` reports this.
- **No screenshots found / invalid index / `scp` failure:** explicit message,
  surfaced to the user by Claude.

## Security notes

- The SSH key is passphraseless by necessity (non-interactive `scp`). Exposure is
  limited to Jarvis→Mac access over the tailnet; the `from=` restriction in
  `authorized_keys` confines its use to Jarvis's Tailscale IP.
- No secrets are written to the repo, the skill, or any output.

## Testing / verification

Manual (SSH + shell; no unit harness):

1. Take a screenshot on the Mac, run `latest`, confirm it lands in `~/screenshots`
   and is readable by Claude.
2. Run `list`, then `pull` two indices; confirm both land.
3. Point the `mac` alias at a bad host (or with the Mac asleep) → confirm a clear,
   non-partial error.
4. `touch -d '2 days ago' ~/screenshots/old.png` then
   `sudo systemd-tmpfiles --clean` → confirm it is removed.
5. `get-screenshot.sh doctor` → all checks green on a healthy setup.

## Out of scope (YAGNI)

- Distributing the skill as a full plugin/marketplace package.
- Pulling non-screenshot images or arbitrary files.
- Pushing from the Mac side / Taildrop integration.
- Keeping the Mac awake (`caffeinate`) for the server to reach it.
