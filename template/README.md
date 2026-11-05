# My agent mesh

Instructions, memory and settings shared by Claude Code and Codex on all my machines, made with
[agent-mesh](https://github.com/Tjabooo/agent-mesh). Keep this repo **private**: memory holds
details about your machines and projects.

- `AGENTS.md`: every agent's instructions (Claude Code imports it, Codex gets a copy)
- `memory/`: the one shared memory (every Claude Code project's memory folder links here)
- `claude/settings.json`: Claude Code settings every machine shares
- `codex/config.toml`: top-level Codex settings every machine shares
- `bin/agent-mesh`: the tool itself
- `chatgpt.json` (optional): publish readable conversations and memory to a separate private
  GitHub repository. See agent-mesh's README; use its `START_HERE.md` through ChatGPT's GitHub app.

New machine: `git clone <this repo> ~/.agent-mesh && python3 ~/.agent-mesh/bin/agent-mesh setup && agent-mesh timer`
