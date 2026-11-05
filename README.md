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
| `claude/settings.json` | Claude Code settings every machine shares (theme, model effort, auto-compact, permission deny/ask rules, `enableArtifact`, `disableClaudeAiConnectors`, `skillOverrides`). These sync both ways with `~/.claude/settings.json`: change one anywhere and it changes everywhere. Hooks, the permission mode and the allow list stay per machine. |
| `codex/config.toml` | Top-level Codex settings to share (`model`, `model_reasoning_effort`). |
| `bin/agent-mesh` | The tool itself, so a new machine needs only one clone. |
| `bin/cost` | `cost`: what all your Claude Code and Codex chats would have cost at API prices, across every machine (see below). |
| `bin/token-audit` | `token-audit`: where the tokens go: fixed per-session overhead, cache breaks, biggest tool results, re-reads, images, subagents (see below). |
| `hosts.json` (optional) | Your machines, for `cost` and `token-audit` (copy `hosts.example.json`). |

Logins, caches and plugins are never shared. Conversation text can optionally be exported to a
separate private GitHub repository for ChatGPT; this is disabled until configured.

## Read your agent data in normal ChatGPT conversations

1. Create a **private** GitHub repository for your data and initialize it with a README.
2. Copy `chatgpt.example.json` to `<mesh>/chatgpt.json`. Set `repository` to `owner/repo` and list
   your machine hostnames under `hosts`. All machines need authenticated Git push access to it.
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

## Where the tokens go

`token-audit` reads the same Claude Code transcripts on every machine (streamed, never loaded whole)
and shows what fills the context. It sizes the fixed per-session overhead from the request shape
Claude Code records, so you can see each tool schema, the skills listing, CLAUDE.md sections and the
memory index. It also lists API usage and cache hit ratio by project and model, cache breaks with
their cause (idle longer than the cache TTL, compaction, tool list change), the largest tool results,
unfiltered shell output, full-file reads and re-reads, images, subagents and auto-compactions.

```sh
token-audit                # all machines, last 14 days
token-audit -d 7 --local   # this machine, last week
token-audit --json         # the raw numbers
```

Every request re-sends the whole prompt, so a tool result is counted as "carried": its size times the
requests that re-sent it. A 20k-character file read early in a long session can cost a million
cache-read tokens. Other machines run their own copy from `~/.agent-mesh/bin` over SSH.

## What your agents cost

`cost` adds up every Claude Code and Codex conversation on all your machines and prices each API
call at the published per-token rates: the same idea as the "cost" line in Claude Code's `/usage`,
but for both agents and every computer at once. On a subscription (Claude Pro/Max, ChatGPT Plus)
nothing is billed per token, so read it as "what this would have cost on the API".

```
 AI spend · API-equivalent · 3 machines                         2026-11-05 13:12
 ────────────────────────────────────────────────────────────────
 Total                        $629.14   since 2026-10-31 · 6 days · $104.86/day
 Today               $121.48
 Last 7 days         $629.14

 By agent
   Claude Code             $566.03    90%  ██████████████████
   Codex                    $63.10    10%  ██

 By machine
   desktop                 $318.13    51%  ██████████████████
   laptop                  $247.25    39%  █████████████▌
   server                   $63.75    10%  ███▌

 By model               calls    input  cache rd   output       cost
   claude-opus-5-5         6.7k    16.9M      1.5B     6.4M    $565.13
   gpt-6.1-sol             1.6k     5.4M    242.1M     1.2M     $57.19

 Cache    99% of input read from cache · saved $6,373.65 (would be $7,002.79 uncached)
 Activity 107 sessions · 8.4k API calls · 6 active days · $5.88/session
```

It also lists the priciest sessions (by their titles) and a bar per day.

```sh
cost                  # all machines, all time
cost -d 7             # last 7 days (or --since 2026-11-01)
cost --local          # only this machine
cost --top 10         # the 10 priciest sessions
cost --daily          # every day, not just the last 14
cost --json           # the raw numbers
```

How it works: it reads the transcripts the agents already keep (`~/.claude/projects/**/*.jsonl`,
including subagents, and `~/.codex/sessions/**/*.jsonl`), takes the token usage of each API call
(counting a message only once when a transcript is forked or resumed), and prices it with the table
at the top of `bin/cost`: input, output, cache writes (5-minute and 1-hour) and cache reads, fast
mode, and long-prompt rates. The Claude prices match the `costUSD` Claude Code records itself. A
model it doesn't know yet shows "no price" until you add it to the table.

Other machines are read over SSH: `cost` pipes itself to their Python, so they need nothing
installed, only an SSH alias that works without a password. List them in `hosts.json` in the mesh
(or point `COST_HOSTS_FILE` somewhere else):

```json
[{"alias": "desktop", "hostname": "desktop", "os": "linux"},
 {"alias": "server", "hostname": "server", "os": "windows"}]
```

The machine you run it on is recognised by its hostname and read directly. A machine that doesn't
answer (asleep, offline) is reported as "not counted" instead of quietly left out.

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

On Linux/macOS, `~/.local/bin/agent-mesh` and `~/.local/bin/cost` are linked to the mesh's copies
(on Windows, run `python %USERPROFILE%\.agent-mesh\bin\cost`). To keep the mesh somewhere
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
