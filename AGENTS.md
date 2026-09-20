# Agent contract

Four runtimes share this CLI. Do not mix their machines:

| Runtime | Where `vtc` runs | Session |
|---|---|---|
| Cursor (this repo) | Student's Mac | `~/.local/share/vtc/` on the Mac |
| Codex | Student's Mac / worktree | Mac session |
| Kian | Student's Mac (OpenClaw) | Mac session |
| Grok Bot | Persistent **cloud Linux computer** | `~/.local/share/vtc/` **on that VM** |

Grok Bot phone/web/desktop all use the cloud computer, not the Mac. See `GROKBOT.md`.

Do not mix login with chat. Agents only run read-only `vtc moodle … --json` and `vtc myportal … --json` after a human login on **that same machine**.

Load `.cursor/skills/vtc-cli/SKILL.md` before Moodle/MyPortal work **in Cursor**.

- Cursor binary: `"/Users/ouqixi/vtc cli/.venv/bin/vtc"`
- Grok Bot binary: `/workspace/vtc-cli/.venv/bin/vtc`
- Moodle `--site ay2526|ay2627` is required; MyPortal has no `--site`
- Never run `vtc login` from an agent chat (Grok Bot: student takes over Agent Computer)
- Never run `vtc myportal apply` or `vtc myportal select`
- Never print passwords, TOTP, cookies, tokens, or storage JSON
- Never copy `*.storage.json` between Mac and the Grok Bot VM
- Never use a proxy browser as a Moodle/MyPortal fallback
- `unverified` means unread, not empty
- PDF/Word/PPT: `sync` then `extract`; paste excerpts, not whole lectures or transcripts
