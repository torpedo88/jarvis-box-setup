# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

This is **not a software project** — there is no source code, build system, or test suite. It is an execution runbook (`setup-plan.md`) for provisioning a single physical machine into a headless always-on local AI server ("Jarvis"). Working in this repo means running shell commands on that actual host to bring it to the target state, then verifying each step.

`setup-plan.md` is the source of truth. Read it in full before acting; the steps are ordered and dependent (driver → container runtime → model server → app stack → networking).

## Target end-state architecture

The plan builds a layered stack on the local box:

- **GPU layer** — NVIDIA driver on the RTX 2060 (6GB VRAM), then the NVIDIA Container Toolkit so Docker can use the GPU.
- **Model server** — Ollama running as a systemd service, bound to `0.0.0.0:11434`, configured to keep one model (`hermes3:8b`) loaded permanently (`OLLAMA_KEEP_ALIVE=-1`, `OLLAMA_NUM_PARALLEL=1`). This is the "Jarvis brain."
- **App stack** — a Docker Compose project at `/opt/stacks/ai/` (owned by user `alienware`): Open WebUI (`:3000`) talking to host Ollama via `host.docker.internal`, plus Qdrant (`:6333`/`:6334`) for vector storage.
- **Networking** — Tailscale for remote access plus `ufw` (deny incoming, allow ssh + the `tailscale0` interface). The box is reached from elsewhere at `http://<tailscale-or-local-ip>:3000`.

The 6GB VRAM budget is why exactly one 8B model is pinned in memory — model choices are constrained by it.

## Critical operational constraints (from the plan)

These override default behavior and must be honored when executing:

- **Show the exact command before running it**, especially anything with `sudo` or `rm`.
- **Stop and ask before any destructive operation.** On any failure, STOP — do not work around or paper over errors.
- **No snap packages** anywhere. Install Docker from Docker's official apt repo (not snap, not Ubuntu's apt); Ollama, Tailscale, and the NVIDIA toolkit from their official sources.
- **Secure Boot is ENABLED.** Installing the NVIDIA driver triggers MOK enrollment — pause and walk the user through the blue MOK Manager screen that appears after reboot. Warn before any reboot.
- Several steps require pausing for the user to act outside the shell (MOK password/enrollment, `tailscale up` browser auth). Pause at those points rather than assuming.

## Verification

There is no `make test` here. Correctness is the 12-item **Final verification checklist** at the bottom of `setup-plan.md` (nvidia-smi, GPU-in-Docker, ollama service/model loaded, both containers up, Open WebUI and Qdrant health endpoints, Tailscale status, ufw rules, lid-switch config). Run each and report results individually; verify a step before moving to the next.

## Environment facts

- Host user: `alienware`; Ubuntu Server (headless), dual-boot with Windows on a separate disk.
- Ethernet interface: `enp111s0`; intended timezone `America/Los_Angeles`.
- Lid switch is set to `ignore` (all three `HandleLidSwitch*` settings) so the laptop stays running closed.
