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

Optional overrides:
```bash
MODEL=llama3.1:8b PROJECTS_DIR=$HOME/code/mnemosyne \
  bash /mnt/c/Users/austi/AppData/Mnemosyne-Setup/install-mnemosyne.sh
```

The script is **idempotent** — re-running it pulls latest from both repos, re-syncs deps, and skips anything already done.

## What it does, in order

1. Verifies `git`, `curl`, `python3 >= 3.11`, `python3-venv`.
2. Installs Ollama (official script) if missing; starts `ollama serve` if the daemon isn't responding on `:11434`.
3. Pulls `qwen3:8b` (or your override) if not already present.
4. Creates `~/projects/mnemosyne/`, clones both repos.
5. Creates venv at `~/projects/mnemosyne/.venv`.
6. `pip install -r eternal-context/skills/eternal-context/requirements.txt`
7. `pip install -e fantastic-disco[dev]`
8. Drops a `.pth` file into the venv so `import eternalcontext` works from anywhere.
9. Smoke-tests both imports.

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

**`sentence-transformers` install hangs** → it pulls torch (~2 GB). Be patient; consider `pip install --no-cache-dir` if you're disk-constrained.

**`ImportError: No module named 'eternalcontext'`** → the `.pth` link didn't write. Fix: `echo $HOME/projects/mnemosyne/eternal-context/skills/eternal-context >> $(python -c 'import site;print(site.getsitepackages()[0])')/eternalcontext.pth`

## Security note

Both repos belong to your own GitHub account (`atxgreene`). The bootstrap pulls only from those two URLs over HTTPS plus the official Ollama installer. No third-party npm packages, no curl-pipe-bash from unknown sources beyond Ollama's own installer.

The recent npm `strapi-plugin-*` supply-chain attack (the one in the Hacker News article) does not affect this stack — Mnemosyne is pure Python and Ollama is Go. OpenClaw was also audited and found clean of those IoCs.
