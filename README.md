# agent-mesh

One brain for your coding agents across all your machines. **Claude Code** and **Codex** get the
same instructions, the same memory and the same settings on every computer. A memory saved on your
laptop is there when you continue on your desktop, and so is a rule you gave Codex when Claude Code
picks up the work.

It's a small Python script (standard library only) and a git repo you own: the **mesh**. Each
machine keeps a clone in `~/.agent-mesh`, and syncs through any git remote: a private GitHub repo,
or a bare repo on one of your machines.

| In the mesh | How each machine uses it |
| --- | --- |
| `AGENTS.md` | Every agent's instructions. Claude Code imports it (`~/.claude/CLAUDE.md` starts with `@~/.agent-mesh/AGENTS.md`; anything below stays local to that machine). Codex gets a copy as `~/.codex/AGENTS.md`, with the memory index and the newest memories inline; older memories are read on demand. |
| `memory/` | The one memory. Every Claude Code project's memory folder (`~/.claude/projects/*/memory`) becomes a link to it (a junction on Windows). `MEMORY.md` is the index. Tell Codex in AGENTS.md to save memories here too. |
| `claude/settings.json` | Claude Code settings every machine shares (theme, model effort, auto-compact, permission deny/ask rules). These sync both ways with `~/.claude/settings.json`: change one anywhere and it changes everywhere. Hooks, the permission mode and the allow list stay per machine. |
| `codex/config.toml` | Top-level Codex settings to share (`model`, `model_reasoning_effort`). |
| `bin/agent-mesh` | The tool itself (plus `agent_mesh_export.py` for the optional ChatGPT export), so a new machine needs only one clone. |

Logins, caches and plugins are never shared. Conversation text can optionally be exported to a
separate private GitHub repository for ChatGPT ([see below](#optional-read-your-agent-data-in-chatgpt));
this is off until configured.

## When it syncs

- **Claude Code**: hooks run `agent-mesh sync --background` at the start of every session and after
  every reply.
- **Codex**: its after-turn `notify` command runs a sync, then whatever notify command you had before.
- **A 10-minute timer** (`agent-mesh timer`) for changes made outside the agents: a systemd user
  timer on Linux, a Task Scheduler task on Windows. Without systemd (e.g. macOS), run
  `agent-mesh sync` from cron or launchd instead.
- **By hand**: `agent-mesh sync`. `agent-mesh status` shows whether this machine is in step.

A sync commits this machine's changes, pulls and merges the others', pushes, and then applies the
result. Markdown merges line by line (`merge=union`), so two machines saving memories at the same
time both keep theirs. If a merge still fails, the machine is left as it was and the failure is
logged in `.git/agent-mesh.log`.

## Set up

Requirements: Python 3.12+ and git on each machine. Your git remote must be reachable without a
password prompt (SSH keys, or a credential helper).

**First machine:** make a new **private** repo for the mesh (for example on GitHub). Memory will hold
details about your machines and projects. Then:

```sh
git clone https://github.com/Tjabooo/agent-mesh.git
python3 agent-mesh/bin/agent-mesh init --origin git@github.com:you/my-agent-mesh.git
agent-mesh timer
```

`init` creates `~/.agent-mesh` with a starter `AGENTS.md`, moves your existing Claude Code project
memories into it, takes the shared settings from this machine (so nothing you've set is lost), wires
up Claude Code and Codex, and pushes. Then fill in `~/.agent-mesh/AGENTS.md`: who you are, your
machines, where your projects live, and how you like agents to work.

**Every other machine:**

```sh
git clone git@github.com:you/my-agent-mesh.git ~/.agent-mesh
python3 ~/.agent-mesh/bin/agent-mesh setup
agent-mesh timer
```

On Windows, use `python` instead of `python3`, and `python %USERPROFILE%\.agent-mesh\bin\agent-mesh`
instead of `agent-mesh`. `timer` creates a Task Scheduler task there.

On Linux/macOS, `~/.local/bin/agent-mesh` is linked to the mesh's copy. To keep the mesh somewhere
other than `~/.agent-mesh`, set `AGENT_MESH_DIR`.

### A bare repo on your own machine instead of GitHub

```sh
ssh myserver 'git init --bare -b main ~/agent-mesh.git'
python3 agent-mesh/bin/agent-mesh init --origin myserver:agent-mesh.git
```

If the server runs Windows with OpenSSH, its default shell is cmd.exe, which keeps git's quotes.
Run git's server side through PowerShell instead:

```sh
git clone -u "powershell git-upload-pack" -c remote.origin.uploadpack="powershell git-upload-pack" \
  -c remote.origin.receivepack="powershell git-receive-pack" myserver:C:/Users/me/agent-mesh.git ~/.agent-mesh
```

## Commands

```text
agent-mesh init [--origin URL]   make a new mesh in ~/.agent-mesh (once, on your first machine)
agent-mesh setup                 on each machine after init or a clone: link memory, import AGENTS.md, add hooks
agent-mesh sync [--background]   commit this machine's changes, merge the others', push, apply locally
agent-mesh timer                 also sync every 10 minutes
agent-mesh status                what is linked and whether this machine is in step
agent-mesh export-chatgpt        retry just the ChatGPT export
agent-mesh codex-notify          Codex's after-turn notify command (set up by sync)
```

## Two Claude accounts, one agent

If you run a second Claude login with `CLAUDE_CONFIG_DIR=~/.claude-2 claude` (for example, to switch
when one hits its usage limit), every sync treats each `~/.claude-<name>` folder as an extra
account. It points that account's `CLAUDE.md`, `settings.json`, `keybindings.json`, `skills`,
`agents`, `commands`, `plugins`, `projects` (and so the memory) and `file-history` at `~/.claude`.
Both accounts then behave as the same agent, and `--resume` finds chats started under the other
one. Each account keeps its own login, `.claude.json` and prompt history.

If Claude Code replaces one of those links with a real file when it saves, the next sync keeps the
newer version and relinks it. The older copy goes to `.git/account-backups/`. On Windows, shared
files are copies kept in step, because a file symlink needs admin rights there; folders are
junctions.

## Optional: read your agent data in ChatGPT

1. Create a **private** GitHub repository for your data and initialize it with a README.
2. Copy `chatgpt.example.json` to `<mesh>/chatgpt.json`. Set `repository` to `owner/repo` and list
   your machine hostnames under `hosts`. All machines need authenticated Git push access to it.
   The export code is `bin/agent_mesh_export.py`; `init` copies it into the mesh. If your mesh is
   older than that, copy it into `<mesh>/bin/` yourself.
3. Run `agent-mesh sync` on each machine. Future syncs publish automatically through the existing
   Claude Code/Codex hooks and 10-minute timer. `agent-mesh export-chatgpt` retries just the export.
4. In a regular chat on chatgpt.com, select the GitHub app and ask:
   **Use GitHub to read START_HERE.md in owner/repo, then find the latest agent work on my topic.**
   Give the GitHub app access to the private repository if your connection restricts repositories.

The archive includes all available user/assistant conversation text (Claude Code, alternate
accounts, Codex sessions and archived sessions), including older history. Shared memory,
instructions and intentionally shared settings are copied too. Each machine owns its directory,
with dates, source paths, indexes and small Markdown parts for long conversations. Direct file
reads work before GitHub's search index catches up. This makes data available through the app;
it does not insert chats into ChatGPT's history or automatically load all files into every chat.

Login files, hidden reasoning, raw tool calls/results, and binary attachments are excluded.
Recognizable tokens, private keys and password/key assignments are redacted from text. Redaction
is best effort: conversations can still contain personal or sensitive information. The exporter
refuses a publicly visible destination; keep the archive private. Removing a local session removes
its current exported files, while previous Git commits retain old versions.

`agent-mesh status` reports counts and the last export result. If GitHub is unavailable, local
mesh sync still completes and the archive retries next time. Export checkouts and incremental
caches stay under the mesh's `.git/` directory. To stop publishing, set `enabled` to `false`.

## Tips for AGENTS.md

The starter file has sections that worked well in practice:

- **About me**: standing preferences and permissions ("always include a commit message", "push
  without asking").
- **One brain**: tell agents to save memories in the mesh, to leave a handover memory before
  finishing unfinished work, and to run `agent-mesh sync` before continuing someone else's work.
- **My machines**: names, OS, how to reach each one (SSH aliases).
- **What lives where**: each project's source of truth, how to deploy or restart it.

## Development

```sh
python -m unittest discover -s tests -v
```

## License

MIT, see [LICENSE](LICENSE).
