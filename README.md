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
| `AGENTS.md` | Every agent's instructions. Claude Code imports it (`~/.claude/CLAUDE.md` starts with `@~/.agent-mesh/AGENTS.md`; anything below stays local to that machine). Codex gets a copy as `~/.codex/AGENTS.md`, with all the shared memory written out, because it can't load memory on demand. |
| `memory/` | The one memory. Every Claude Code project's memory folder (`~/.claude/projects/*/memory`) becomes a link to it (a junction on Windows). `MEMORY.md` is the index. Tell Codex in AGENTS.md to save memories here too. |
| `claude/settings.json` | Claude Code settings every machine shares (theme, model effort, auto-compact, permission deny/ask rules). These sync both ways with `~/.claude/settings.json`: change one anywhere and it changes everywhere. Hooks, the permission mode and the allow list stay per machine. |
| `codex/config.toml` | Top-level Codex settings to share (`model`, `model_reasoning_effort`). |
| `bin/agent-mesh` | The tool itself, so a new machine needs only one clone. |

Logins, chat history, caches and plugins are never shared.

## When it syncs

- **Claude Code**: hooks run `agent-mesh sync --background` at the start of every session and after
  every reply.
- **Codex**: its after-turn `notify` command runs a sync, then whatever notify command you had before.
- **A 10-minute timer** (`agent-mesh timer`) for changes made outside the agents.
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

## Tips for AGENTS.md

The starter file has sections that worked well in practice:

- **About me**: standing preferences and permissions ("always include a commit message", "push
  without asking").
- **One brain**: tell agents to save memories in the mesh, to leave a handover memory before
  finishing unfinished work, and to run `agent-mesh sync` before continuing someone else's work.
- **My machines**: names, OS, how to reach each one (SSH aliases).
- **What lives where**: each project's source of truth, how to deploy or restart it.

## License

MIT
