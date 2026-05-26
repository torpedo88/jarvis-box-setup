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

### 5. Ollama + Hermes (the Jarvis brain)
- Install Ollama via official install script: curl -fsSL https://ollama.com/install.sh | sh
- Configure systemd override (sudo systemctl edit ollama) to add:
  Environment="OLLAMA_HOST=0.0.0.0:11434"
  Environment="OLLAMA_KEEP_ALIVE=-1"
  Environment="OLLAMA_NUM_PARALLEL=1"
- Reload and restart ollama
- Pull only: hermes3:8b
- Verify model stays loaded: ollama ps after running ollama run hermes3:8b "hi"

### 6. Docker Compose stack at /opt/stacks/ai/
- Create /opt/stacks/ai/ owned by alienware user
- docker-compose.yml with:
  - open-webui: ghcr.io/open-webui/open-webui:main
    - port 3000:8080
    - env OLLAMA_BASE_URL=http://host.docker.internal:11434
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
