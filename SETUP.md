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

## Configure channels (wizard)

After the bootstrap finishes, run the interactive wizard to set up channel credentials:

```bash
bash /mnt/c/Users/austi/AppData/Mnemosyne-Setup/mnemosyne-wizard.sh
```

The wizard:

1. **LLM backend** — confirms `OLLAMA_HOST` + `OLLAMA_MODEL`, validates the daemon is responding and the model is pulled.
2. **Telegram channel** — prompts for a bot token, validates it against `https://api.telegram.org/bot<token>/getMe`, then auto-detects your chat ID by polling `getUpdates` after you message the bot. (Other channels — Discord/Slack/REST — are roadmap; the wizard preserves any keys you add by hand.)
3. **Obsidian skill (preview)** — captures `OBSIDIAN_VAULT_PATH` for the upcoming Obsidian skill. Only writes the env var; the skill module itself isn't wired up yet (see roadmap below).
4. **Writes `~/projects/mnemosyne/.env`** with mode `600`, backing up any previous version to `.env.bak.<timestamp>`.

The wizard is **safe to re-run** — it reads the existing `.env`, offers current values as defaults, and preserves any keys it doesn't manage (so Discord/Slack/REST credentials you add by hand survive a re-run). Nothing in `.env` is ever committed to either repo.

## Boot the agent

```bash
source ~/projects/mnemosyne/.venv/bin/activate
set -a; . ~/projects/mnemosyne/.env; set +a   # load wizard-written creds

# CLI REPL (base agent — proves the stack is healthy)
cd ~/projects/mnemosyne/eternal-context/skills/eternal-context
python -m eternalcontext

# Multi-channel server (uses Telegram if you configured it via the wizard)
python -m eternalcontext.server

# Run consciousness-extension tests
cd ~/projects/mnemosyne/fantastic-disco
pytest mnemosyne/tests/ -v
```

### Entrypoint choice: base agent vs. ConsciousnessLoop

Two valid boot paths once the venv is ready:

- **`python -m eternalcontext`** — base agent only. ICMS, SDI, tools, channels. Skips the consciousness layer. Use this **first** to verify Ollama, ICMS, the `.pth` link, and the channel adapter all work.
- **`python -m mnemosyne`** (or programmatic `from mnemosyne import ConsciousnessLoop`) — wraps `eternalcontext` with TurboQuant, metacognition, dream consolidation, autobiography, behavioral coupling. This is the actual product surface.

Recommendation: validate `python -m eternalcontext` first; switch the daily-driver entrypoint to `ConsciousnessLoop` only after you've confirmed the base agent is healthy. Don't make the switch as the *first* run — too many things can fail at once.

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

## First-run validation checklist

Run this from `~/projects/mnemosyne/eternal-context/skills/eternal-context` after `source ~/projects/mnemosyne/.venv/bin/activate`:

```bash
ollama list                       # confirm qwen3:8b row exists
python -c "import eternalcontext, mnemosyne; print('imports ok')"
python -m eternalcontext --help   # confirm CLI loads (no traceback)
python -m eternalcontext          # boot the REPL — should hit Ollama on first prompt
```

A successful boot proves: venv intact, `.pth` link working, eternal-context requirements installed, fantastic-disco editable install resolving, Ollama daemon up, model present. If any step fails, re-run the bootstrap — it's idempotent.

## Roadmap: Obsidian skill

The wizard captures `OBSIDIAN_VAULT_PATH` in `.env` so the path is ready. The actual skill module is **not yet implemented** — it needs to be added to `eternal-context/skills/` and registered with the agent's tool registry.

Open questions before writing the skill module (paste answers / a representative existing skill into the next session):

1. **Skill interface.** What shape do skills under `eternal-context/skills/*` actually take? Is each skill a Python module exposing a registry-discoverable class, a YAML manifest with code-behind, a folder with `__init__.py` + `tool.py`, or something else? The Obsidian skill should mirror whatever pattern the existing 11 tools use.
2. **Indexing strategy.** v1 should be **ripgrep** over the vault (fast, deterministic, no model dependency). Vector embeddings via sentence-transformers can be a v2 if ripgrep proves insufficient — torch is already in the venv either way.
3. **Read-only or read-write?** Recommend **read-only for v1**. Daily-note appending and link rewriting are useful but blast-radius-large; better to land them as a separate `obsidian-write` skill once the read path is solid.
4. **Frontmatter.** Should YAML frontmatter (tags, aliases, dataview fields) be exposed as separate query surfaces (e.g. `search_by_tag`), or treated as flat text inside the note body for v1? Lean v1: flat text. v2: structured.
5. **Tool surface.** Reasonable v1 tools: `obsidian_search(query, limit=10)`, `obsidian_read(path)`, `obsidian_list_recent(days=7)`. All read from `OBSIDIAN_VAULT_PATH`. No write tools.
6. **WSL path translation.** If the vault lives on the Windows side (`/mnt/c/Users/austi/Documents/Obsidian`), file watch performance is mediocre. Acceptable for v1 (queries only). If it becomes a problem, mirror to a WSL-native path or use `inotify` against the `/mnt/c` path with a longer poll interval.

Once you can paste an existing skill file from `eternal-context/skills/`, the Obsidian skill drops in alongside it as a small additional module. The wizard already wires the env var.

## Security note

Both repos belong to your own GitHub account (`atxgreene`). The bootstrap pulls only from those two URLs over HTTPS plus the official Ollama installer. No third-party npm packages, no curl-pipe-bash from unknown sources beyond Ollama's own installer.

The recent npm `strapi-plugin-*` supply-chain attack (the one in the Hacker News article) does not affect this stack — Mnemosyne is pure Python and Ollama is Go. OpenClaw was also audited and found clean of those IoCs.
