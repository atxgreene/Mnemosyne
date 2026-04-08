# Mnemosyne Setup (WSL2 / Ubuntu)

A reproducible install of the Mnemosyne agent stack alongside (not replacing) OpenClaw.

## What gets installed

| Component | Repo | Role |
|---|---|---|
| `eternal-context` | atxgreene/eternal-context | Base agent: 3-tier ICMS memory, SDI selection, model routing, channel adapters, tool registry |
| `fantastic-disco` | atxgreene/fantastic-disco @ `claude/review-mnemosyne-agent-5bb7m` | Consciousness extensions: TurboQuant, metacognition, dream consolidation, autobiography, behavioral coupling |
| Ollama | ollama.com | Local LLM runtime |
| `qwen3:8b` | via Ollama | Default local model (override with `MODEL=...`) |

`fantastic-disco` is **not** standalone — it imports and wraps `eternalcontext`. Both must be installed.

## Prereqs

- WSL2 with Ubuntu (24.04 recommended; 22.04 works but you may need `python3.11` from deadsnakes)
- `git`, `curl`, `python3 >= 3.11`, `python3-venv`
- ~10 GB free disk for the model + venv
- Optional: GPU passthrough into WSL if you want faster inference (CPU works, just slower)

Install missing prereqs once:
```bash
sudo apt update
sudo apt install -y git curl python3 python3-venv python3-pip
```

## Run the bootstrap

The script lives at `C:\Users\austi\AppData\Mnemosyne-Setup\install-mnemosyne.sh`. From WSL:

```bash
bash /mnt/c/Users/austi/AppData/Mnemosyne-Setup/install-mnemosyne.sh
```

Optional overrides (all env vars):
```bash
MODEL=llama3.1:8b PROJECTS_DIR=$HOME/code/mnemosyne \
  bash /mnt/c/Users/austi/AppData/Mnemosyne-Setup/install-mnemosyne.sh

# Skip the ~2GB CUDA torch download — install CPU-only wheels (~200MB) instead.
# Useful on hosts without GPU passthrough or when you don't care about
# embedding-model speed.
CPU_TORCH=1 bash /mnt/c/Users/austi/AppData/Mnemosyne-Setup/install-mnemosyne.sh
```

The script is **idempotent** — re-running it pulls latest from both repos, re-syncs deps, and skips anything already done. Partial-failure re-runs always re-write the `eternalcontext.pth` link via an `EXIT` trap, so a crashed run never leaves the venv in a half-linked state.

## What it does, in order

1. Verifies `git`, `curl`, `python3 >= 3.11`, `python3-venv`.
2. Installs Ollama (official script) if missing; starts `ollama serve` if the daemon isn't responding on `:11434`.
3. Pulls `qwen3:8b` (or your override) if not already present.
4. Creates `~/projects/mnemosyne/`, clones both repos.
4b. **Patches** `fantastic-disco/pyproject.toml` — upstream ships `build-backend = "setuptools.backends._legacy:_Backend"` which doesn't exist; rewritten to `setuptools.build_meta` before pip ever sees it.
5. Creates venv at `~/projects/mnemosyne/.venv`.
5b. **Writes `eternalcontext.pth` early** (before any `pip install`) and re-writes on `EXIT` so partial-failure re-runs always self-heal.
5c. If `CPU_TORCH=1`, installs CPU-only torch wheels from the pytorch CPU index *before* the eternal-context requirements, so pip sees torch as already-satisfied and skips the ~2GB CUDA download.
6. `pip install -r eternal-context/skills/eternal-context/requirements.txt`
7. `pip install -e fantastic-disco[dev]`
8. Smoke-tests both imports (`import eternalcontext, mnemosyne`).

## After install

```bash
source ~/projects/mnemosyne/.venv/bin/activate

# CLI REPL (the main interactive agent)
cd ~/projects/mnemosyne/eternal-context/skills/eternal-context
python -m eternalcontext

# Multi-channel server (only if you set TELEGRAM_BOT_TOKEN / etc. first)
python -m eternalcontext.server

# Run consciousness-extension tests
cd ~/projects/mnemosyne/fantastic-disco
pytest mnemosyne/tests/ -v
```

## How this co-exists with OpenClaw

- OpenClaw lives at `C:\Users\austi\AppData\Roaming\npm\node_modules\openclaw` and runs as a Windows login item ("OpenClaw Gateway"). **Untouched.**
- Mnemosyne lives in WSL at `~/projects/mnemosyne/`. Different process tree, different filesystem, different ports (Ollama 11434, Mnemosyne REST 8000 if you start the server). No collisions.
- Run them in parallel for as long as you want. Once Mnemosyne covers your daily workflows, you can uninstall OpenClaw separately.

## Uninstall

```bash
# Remove the Mnemosyne install entirely (does not touch Ollama or models)
rm -rf ~/projects/mnemosyne

# Optionally remove Ollama models
ollama rm qwen3:8b

# Optionally remove Ollama itself (Linux)
sudo rm /usr/local/bin/ollama
sudo rm -rf /usr/share/ollama
```

## Troubleshooting

**`python3: command not found`** → `sudo apt install -y python3 python3-venv python3-pip`

**`Python 3.11+ required` but you're on Ubuntu 22.04** →
```bash
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt install -y python3.11 python3.11-venv
PY=python3.11 bash install-mnemosyne.sh   # then edit the script's python3 calls, or just upgrade WSL to 24.04
```

**`Ollama failed to start`** → check `/tmp/ollama.log`. Most common cause: another process on :11434, or WSL2 systemd not enabled. Workaround: `nohup ollama serve &` manually.

**`sentence-transformers` install hangs** → it pulls torch (~2 GB CUDA wheels by default). Re-run with `CPU_TORCH=1` to use the CPU-only index instead (~200 MB), or `pip install --no-cache-dir` if you're disk-constrained.

**`ImportError: No module named 'eternalcontext'`** → the `.pth` link didn't write. The bootstrap now writes it both early (before pip install) and again on `EXIT` via a trap, so this should never happen on a fresh run. If it does, just re-run the bootstrap — it will rewrite the link without re-installing anything.

**`pip install -e fantastic-disco[dev]` fails with `Cannot import 'setuptools.backends._legacy'`** → upstream pyproject.toml bug. The bootstrap auto-patches this on clone (step 4b). If you cloned manually, run:
```bash
sed -i 's|setuptools\.backends\._legacy:_Backend|setuptools.build_meta|' \
  ~/projects/mnemosyne/fantastic-disco/pyproject.toml
```

## Open decisions (post-install)

These can't be answered by the bootstrap script — they need a first-run walkthrough on the target host. Recording them here so we don't lose them.

### 1. Channel + credentials

The bootstrap creates an empty venv-side environment. It does **not** create `~/projects/mnemosyne/.env`, because credentials must never live inside either repo and the right channel set depends on you. Pick at least one of the four channels eternal-context exposes:

| Channel | Required env vars | Notes |
|---|---|---|
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS` | Lowest-friction. BotFather token + your numeric chat ID. |
| Discord | `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID` | Needs a Discord application + bot user with message-content intent. |
| Slack | `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET` | Socket Mode is easiest in WSL — no inbound webhook to expose. |
| REST | `MNEMOSYNE_REST_HOST`, `MNEMOSYNE_REST_PORT` | Local-only HTTP. Good for scripts and CLI clients. Default `127.0.0.1:8765`. |

Once decided, drop them in `~/projects/mnemosyne/.env` (mode `600`) and load it from your shell rc or via `set -a; . ~/projects/mnemosyne/.env; set +a` before launching the agent. **Never** commit `.env` to either repo.

### 2. Entrypoint: `eternalcontext` direct vs. `mnemosyne` ConsciousnessLoop

Two valid boot paths:

- **`python -m eternalcontext`** — base agent only. ICMS, SDI, tools, channels. Skips the consciousness layer entirely. Use this first to verify the stack is healthy and Ollama is reachable.
- **`python -m mnemosyne`** (or programmatic `from mnemosyne import ConsciousnessLoop`) — wraps eternalcontext with TurboQuant, metacognition, dream consolidation, autobiography, behavioral coupling. This is the actual product surface.

Recommendation: validate `python -m eternalcontext` boots first (proves Ollama, ICMS, channel adapter, .pth link, requirements). Then switch the daily-driver entrypoint to `ConsciousnessLoop` once you've confirmed the consciousness extensions don't error on your machine. Don't make the switch as the *first* run — too many things can fail at once.

### 3. First-run validation checklist

Run this from `~/projects/mnemosyne/eternal-context/skills/eternal-context` after `source ~/projects/mnemosyne/.venv/bin/activate`:

```bash
ollama list                       # confirm qwen3:8b row exists
python -c "import eternalcontext, mnemosyne; print('imports ok')"
python -m eternalcontext --help   # confirm CLI loads (no traceback)
python -m eternalcontext          # boot the REPL — should hit Ollama on first prompt
```

A successful boot proves: venv intact, .pth link working, eternal-context requirements installed, fantastic-disco editable install resolving, Ollama daemon up, model present. If any step fails, re-run the bootstrap — it's idempotent.

## Security note

Both repos belong to your own GitHub account (`atxgreene`). The bootstrap pulls only from those two URLs over HTTPS plus the official Ollama installer. No third-party npm packages, no curl-pipe-bash from unknown sources beyond Ollama's own installer.

The recent npm `strapi-plugin-*` supply-chain attack (the one in the Hacker News article) does not affect this stack — Mnemosyne is pure Python and Ollama is Go. OpenClaw was also audited and found clean of those IoCs.
