# agent-mesh

One brain for Claude Code across all your machines: the same instructions, the same memory and the
same settings on every computer. A memory saved on your laptop is there when you continue on your
desktop.

It's a small Python script (standard library only) and a git repo you own: the **mesh**. Each
machine keeps a clone in `~/.agent-mesh`, and syncs through any git remote: a private GitHub repo,
or a bare repo on one of your machines.

| In the mesh | How each machine uses it |
| --- | --- |
| `AGENTS.md` | Every agent's instructions. Claude Code imports it (`~/.claude/CLAUDE.md` starts with `@~/.agent-mesh/AGENTS.md`; anything below stays local to that machine). |
| `memory/` | The one memory. Every Claude Code project's memory folder (`~/.claude/projects/*/memory`) becomes a link to it (a junction on Windows). `MEMORY.md` is the index. |
| `claude/settings.json` | Claude Code settings every machine shares (theme, model effort, auto-compact, permission deny/ask rules, `enableArtifact`, `disableClaudeAiConnectors`, `skillOverrides`). These sync both ways with `~/.claude/settings.json`: change one anywhere and it changes everywhere. Hooks, the permission mode and the allow list stay per machine. |
| `bin/agent-mesh` | The tool itself, so a new machine needs only one clone. |
| `bin/agent_mesh_sessions.py` | Copies the other machines’ Claude Code sessions here, so you can resume a chat on any machine (see below). |
| `bin/cost` | `cost`: what all your Claude Code chats would have cost at API prices, across every machine (see below). |
| `bin/token-audit` | `token-audit`: where the tokens go: fixed per-session overhead, cache breaks, biggest tool results, re-reads, images, subagents (see below). |
| `hosts.json` (optional) | Your machines, for session sharing, `cost` and `token-audit` (copy `hosts.example.json`). |

Logins, caches and plugins are never shared.

## Resume a chat on another machine

Every sync also copies the other machines' Claude Code sessions to this one
(`~/.claude/projects/**`). So a chat you ran on the laptop shows up in `claude --resume` on the
desktop, and you carry on there.
`agent-mesh sessions` does it right away. Automatic pulls run at most every 2 minutes, because the
hooks sync after every reply.

- Each machine pulls from its peers in `hosts.json` (falling back to `~/.config/cost/hosts.json` or
  `~/.config/archfleet/hosts.json`) over SSH. Peers need only `ssh`, `tar` and `python3`.
  A sleeping machine is skipped and tried again next time.
- Only machines with the same OS share sessions. A session remembers its working folder, and a
  Windows path means nothing on Linux. Claude Code lists a session under its folder, so resume from
  the same folder (`~` → `~`); it needs the same path on both machines.
- Sessions are append-only JSONL. A file is copied when it's missing here, and grown when the peer's
  copy is longer and starts with exactly what's here. Only whole lines are copied, so a chat that's
  still being written is never cut mid-line. A chat continued on two machines at once has diverged:
  it's left alone on both and logged in `.git/agent-mesh.log`.
- Copied bytes are listed in `~/.local/state/agent-mesh/foreign-sessions.json`. `cost`,
  and `token-audit` skip them, so each chat is counted on the machine that ran it.

## When it syncs

- **Claude Code**: hooks run `agent-mesh sync --background` at the start of every session and after
  every reply.
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

`cost` adds up every Claude Code conversation on all your machines and prices each API call at the
published per-token rates: the same idea as the "cost" line in Claude Code's `/usage`, but for every
account and computer at once. On a subscription (Claude Pro/Max) nothing is billed per token, so read it as "what this would have cost on the API".

```
 AI spend · API-equivalent · 3 machines                         2026-11-05 13:12
 ────────────────────────────────────────────────────────────────
 Total                        $566.03   since 2026-10-31 · 6 days · $94.34/day
 Today               $121.48
 Last 7 days         $566.03

 By agent
   Claude Code             $566.03   100%  ██████████████████

 By machine
   desktop                 $318.13    56%  ██████████████████
   laptop                  $247.25    44%  ██████████████
   server                    $0.65     0%  

 By model               calls    input  cache rd   output       cost
   claude-opus-5-5         6.7k    16.9M      1.5B     6.4M    $565.13

 Cache    99% of input read from cache · saved $6,373.65 (would be $7,002.79 uncached)
 Activity 107 sessions · 6.7k API calls · 6 active days · $5.29/session
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
including subagents), takes the token usage of each API call
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
up Claude Code, and pushes. Then fill in `~/.agent-mesh/AGENTS.md`: who you are, your
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
