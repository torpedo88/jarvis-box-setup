---
name: get-screenshot
description: Use when the user wants to view, grab, pull, see, or look at a screenshot or image from their Mac while Claude runs on the headless Jarvis box. Fetches the most recent image(s) from the Mac's ~/Screenshots folder over SSH into ~/screenshots so Claude can read them. Also use when the user pastes a /Users/... Mac path that does not exist on this Linux box, or says they can't paste an image.
---

# get-screenshot

Claude runs on Jarvis (headless Linux) and cannot see the Mac's clipboard, GUI, or `/Users/...` paths. This skill fetches images from the Mac over SSH so Claude can read them.

It pulls the most recent image(s) (`png/jpg/jpeg/gif/webp/heic`) from the Mac's **`~/Screenshots`** drop folder. The user gets an image there by either taking a screenshot configured to save into it, or dragging/saving any image into it.

Script: `~/.claude/skills/get-screenshot/get-screenshot.sh`

## Grab the latest (default, fastest)
Run:

    ~/.claude/skills/get-screenshot/get-screenshot.sh latest

It prints one absolute path under `~/screenshots`. Read that path as an image.

## Let the user pick from a list
1. Run:

       ~/.claude/skills/get-screenshot/get-screenshot.sh list

   Prints a numbered table (index, time, size, name), newest first, and records a manifest.
2. Show the table and ask which index/indices they want.
3. Run (one or several indices):

       ~/.claude/skills/get-screenshot/get-screenshot.sh pull 1 3

   Prints one absolute path per pulled file. Read them as images.

## Troubleshooting
If a fetch fails, run:

    ~/.claude/skills/get-screenshot/get-screenshot.sh doctor

and relay the failed check. Most common causes:
- **Mac asleep or off** — it drops off Tailscale and won't answer (`ssh reachable: NO`).
- **No images in `~/Screenshots`** — ask the user to drop/save the image there. (Note: macOS blocks SSH from reading `~/Desktop`/`~/Documents`/`~/Downloads`, so images must be in `~/Screenshots` or another non-protected folder.)

## Notes
- Files land in `~/screenshots` on Jarvis and auto-delete 24h after they're pulled (systemd-tmpfiles).
- Filenames are sanitized (spaces → underscores) on landing.
- Never reference Mac-side `/Users/...` paths directly — fetch with this skill instead.
- Config via env: `SCREENSHOT_MAC_HOST` (ssh alias, default `mac`), `SCREENSHOT_LANDING_DIR` (default `~/screenshots`).
