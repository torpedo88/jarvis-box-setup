# get-screenshot — install / restore

Pulls images from the Mac into Jarvis over Tailscale SSH so Claude (running headless
on Jarvis) can read them. See `../../docs/superpowers/specs/2026-05-26-screenshot-transfer-design.md`
for the design and `../../docs/superpowers/plans/2026-05-26-get-screenshot.md` for the build.

These files are the source of record; the live copies are installed outside this repo:

| Repo file | Installed to |
|-----------|-------------|
| `skills/get-screenshot/get-screenshot.sh` | `~/.claude/skills/get-screenshot/get-screenshot.sh` (chmod +x) |
| `skills/get-screenshot/SKILL.md` | `~/.claude/skills/get-screenshot/SKILL.md` |
| `etc/tmpfiles.d/jarvis-screenshots.conf` | `/etc/tmpfiles.d/jarvis-screenshots.conf` (root) |

## Reinstall

```bash
install -Dm755 skills/get-screenshot/get-screenshot.sh ~/.claude/skills/get-screenshot/get-screenshot.sh
install -Dm644 skills/get-screenshot/SKILL.md          ~/.claude/skills/get-screenshot/SKILL.md
sudo install -Dm644 etc/tmpfiles.d/jarvis-screenshots.conf /etc/tmpfiles.d/jarvis-screenshots.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/jarvis-screenshots.conf
```

## One-time prerequisites (not scripted)

- **Mac:** Remote Login enabled; Tailscale on the same tailnet as Jarvis; awake when in use.
- **Jarvis `~/.ssh/config`:** a `mac` host alias → Mac's MagicDNS name, user `abhishekmaharjan`,
  `IdentityFile ~/.ssh/jarvis_to_mac`, `IdentitiesOnly yes`, `StrictHostKeyChecking accept-new`.
- **Key:** `~/.ssh/jarvis_to_mac` (passphraseless), public half in the Mac's `~/.ssh/authorized_keys`.
- **Mac screenshot folder:** images must live in `~/Screenshots` (or another non-TCC-protected
  folder). macOS blocks SSH from reading `~/Desktop`, `~/Documents`, `~/Downloads`.

## Usage

```bash
get-screenshot.sh            # pull the most recent image
get-screenshot.sh list       # numbered table of recent images (+ manifest)
get-screenshot.sh pull 1 3   # pull by index from the last list
get-screenshot.sh doctor     # preflight checks
```
