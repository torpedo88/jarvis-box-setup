# R17 Local AI Box (Jarvis) Setup Plan

## Hardware
- Alienware 17 R5, Intel i7-8750H, 32GB RAM
- RTX 2060 Mobile (6GB VRAM)
- Ubuntu Server (headless), dual-boot with Windows on separate disk
- Secure Boot is ENABLED (need MOK enrollment for NVIDIA driver)
- User: alienware
- Ethernet interface: enp111s0

## Goal
Headless always-on AI server. One model (Hermes 3 8B) stays loaded
permanently as Jarvis brain. Will pair with Oracle Cloud A1 later.

## Tasks (execute in order; verify each before moving on)

### 1. NVIDIA stack
- Run `ubuntu-drivers devices` and show me the output first
- Install recommended NVIDIA driver via `sudo ubuntu-drivers install`
- IMPORTANT: Secure Boot is on. The installer will prompt for a MOK
  password. Tell me what to set, then stop and walk me through the
  blue MOK Manager screen that will appear on the laptop after reboot
- Reboot when ready (warn me first)
- After reboot resume, verify with `nvidia-smi` — must show RTX 2060

### 2. Docker + NVIDIA Container Toolkit
- Install Docker from official Docker apt repo (NOT snap, NOT ubuntu apt)
- Add user alienware to docker group
- Install NVIDIA Container Toolkit from NVIDIA's official repo
- Configure Docker daemon for NVIDIA runtime
- Verify: docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi

### 3. System tweaks
- Edit /etc/systemd/logind.conf:
  - HandleLidSwitch=ignore
  - HandleLidSwitchExternalPower=ignore
  - HandleLidSwitchDocked=ignore
- Restart systemd-logind
- Set timezone: sudo timedatectl set-timezone America/Los_Angeles
- Enable unattended-upgrades for security patches only

### 4. Networking
- Install Tailscale via official install script
- Run `sudo tailscale up` and PAUSE so I can auth in browser
- Install ufw:
  - default deny incoming
  - allow ssh (22/tcp)
  - allow from tailscale interface tailscale0
- Enable ufw

#### 4a. Public exposure via Tailscale Funnel (optional)
Goal: reach Open WebUI from the public internet WITHOUT port-forwarding the
router. Funnel relays public HTTPS through tailscaled to localhost:3000, so
ufw stays deny-incoming, the home IP stays hidden, and TLS is auto-provisioned
(Let's Encrypt) on the `*.ts.net` hostname.

Prereqs (Tailscale admin console, browser — cannot be done from the shell):
- DNS → enable MagicDNS + HTTPS Certificates.
- Enable Funnel on the tailnet. `tailscale funnel 3000` prints a one-click
  enable link (`https://login.tailscale.com/f/funnel?node=...`) if not yet on;
  approving it adds the `funnel` nodeAttr to the ACL policy.

Before exposing — lock down Open WebUI auth (it has no rate limiting):
- Create the admin account first (signup must be ON for the first user).
- Then DISABLE signup so the public cannot self-register. Persisted in the
  app DB at config.ui.enable_signup. NOTE: editing webui.db while the container
  runs gets clobbered on shutdown — stop the container, edit, start. Or toggle
  in Admin Panel → Settings → Enable New Sign Ups.
- Add users for others via Admin Panel → Users; send them URL + password.

Bring it up:
- `sudo tailscale funnel --bg 3000`   (needs root, or `tailscale set --operator=$USER` once)
- Live at `https://<host>.<tailnet>.ts.net` ; config persists across reboot.
- Take down: `sudo tailscale funnel --https=443 off`

Reminder: the single GPU slot still bounds throughput (see Ollama concurrency
note). Login-gating stops strangers; it does not add capacity.

### 5. Ollama + Hermes (the Jarvis brain)
- Install Ollama via official install script: curl -fsSL https://ollama.com/install.sh | sh
- Configure systemd override (sudo systemctl edit ollama) to add:
  Environment="OLLAMA_HOST=0.0.0.0:11434"
  Environment="OLLAMA_KEEP_ALIVE=-1"
  Environment="OLLAMA_NUM_PARALLEL=2"
  Environment="OLLAMA_FLASH_ATTENTION=1"
  Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
  Environment="OLLAMA_CONTEXT_LENGTH=2048"
- Reload and restart ollama
- Pull only: hermes3:8b
- Verify model stays loaded: ollama ps after running ollama run hermes3:8b "hi"

  Concurrency vs VRAM (6GB budget). hermes3:8b ~4.9GB resident leaves ~860MB
  headroom. KV-cache footprint = NUM_PARALLEL * CONTEXT_LENGTH, so the two
  settings trade against each other:
    - par=1, ctx=4096 : 1 request at a time (others queue), long context, 100% GPU
    - par=2, ctx=2048 : 2 concurrent requests, 100% GPU, shorter context  ← current
    - par=2, ctx=4096 : 2 concurrent but ~8% spills to CPU (slower)
  After any change, confirm `ollama ps` shows "100% GPU" (not "x%/y% CPU/GPU");
  CPU spill means VRAM overcommit — reduce CONTEXT_LENGTH or NUM_PARALLEL.

### 6. Docker Compose stack at /opt/stacks/ai/
- Create /opt/stacks/ai/ owned by alienware user
- docker-compose.yml with:
  - open-webui: ghcr.io/open-webui/open-webui:main
    - port 3000:8080
    - env OLLAMA_BASE_URL=http://host.docker.internal:11434
    - env BYPASS_MODEL_ACCESS_CONTROL=true
      # Without this, non-admin users get an EMPTY model dropdown: base Ollama
      # models pulled directly have no DB model entry, and default access
      # control (BYPASS=false) hides un-shared models from non-admins. Setting
      # true lets every authenticated account see/select all models — correct
      # for a small trusted multi-user box. Verify: as a 'user' role,
      # GET /api/models returns count > 0.
    - env ENABLE_API_KEYS=true                       # PLURAL name; default False
    - env USER_PERMISSIONS_FEATURES_API_KEYS=true    # let non-admins mint keys
      # Users mint keys in Settings -> Account -> API Keys, then call via the
      # OpenAI-compatible Ollama proxy:
      #   POST /ollama/v1/chat/completions  (Bearer sk-... ; model hermes3:8b)
      # GOTCHA: that /v1 handler does NOT honor BYPASS_MODEL_ACCESS_CONTROL for
      # non-admins on UNREGISTERED base models -> returns 403 "Model not found".
      # Fix: register hermes3:8b as a PUBLIC model so a real model row + public
      # grant exist. As admin:
      #   POST /api/v1/models/model/access/update
      #     {"id":"hermes3:8b","name":"hermes3:8b",
      #      "access_grants":[{"principal_type":"user","principal_id":"*","permission":"read"}]}
      # (principal_id "*" = public read). After that, non-admin keys work on /v1.
      # The /api/chat/completions path 400s on raw calls (needs a chat_id) — UI only.
    - extra_hosts: "host.docker.internal:host-gateway"
    - volume open-webui:/app/backend/data
    - restart: unless-stopped
  - qdrant: qdrant/qdrant:latest
    - ports 6333:6333, 6334:6334
    - volume qdrant_data:/qdrant/storage
    - restart: unless-stopped
- Bring up with `docker compose up -d`
- Verify both containers running

### 7. Final wiring
- Print the Tailscale IP and the local IP so I can hit Open WebUI from
  my Mac at http://<ip>:3000

## Constraints
- No snap packages anywhere
- Stop and ask before any destructive operation
- Show me the command you're about to run before running it,
  especially anything with sudo or rm
- If anything fails, STOP — don't paper over errors

## Final verification checklist (run all at end, report each)
1. nvidia-smi shows RTX 2060 with driver loaded
2. docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi works
3. systemctl is-active ollama → active
4. ollama list shows hermes3:8b
5. ollama ps shows hermes3:8b loaded (after first prompt)
6. ollama run hermes3:8b "say hi" returns a response
7. docker ps shows open-webui and qdrant both Up
8. curl -s http://localhost:3000 returns HTML
9. curl -s http://localhost:6333/healthz returns ok response
10. tailscale status shows connected
11. ufw status shows correct rules
12. cat /etc/systemd/logind.conf | grep HandleLid → all set to ignore
13. ollama ps shows hermes3:8b "100% GPU" (no CPU spill) after a prompt
14. (if Funnel enabled) tailscale funnel status shows the ts.net host proxying
    127.0.0.1:3000; curl -sw '%{http_code}' https://<host>.ts.net/ → 200
15. (if public) GET https://<host>.ts.net/api/config → features.enable_signup
    is false (public cannot self-register)
