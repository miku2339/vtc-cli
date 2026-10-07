# Agent contract

Four runtimes share this CLI. Do not mix their machines:

| Runtime | Where `vtc` runs | Session |
|---|---|---|
| Cursor (this repo) | Student's Mac | `~/.local/share/vtc/` on the Mac |
| Codex | Student's Mac / worktree | Mac session |
| Kian | Student's Mac (OpenClaw) | Mac session |
| Grok Bot | Persistent **cloud Linux computer** | `~/.local/share/vtc/` **on that VM** |

Grok Bot phone/web/desktop all use the cloud computer, not the Mac. See `GROKBOT.md`.

Keep credentials out of chat. Agents may run `vtc login … --credentials-file PATH --json` using a user-prepared private Markdown file on **that same machine**, then run read-only `vtc moodle … --json` and `vtc myportal … --json`.

Load `.cursor/skills/vtc-cli/SKILL.md` before Moodle/MyPortal work **in Cursor**.

- Local binary: resolve `.venv/bin/vtc` from the active repository root; use its absolute path if the shell is elsewhere.
- Grok Bot binary: `/workspace/vtc-cli/.venv/bin/vtc`
- Moodle `--site ay2526|ay2627` is required; MyPortal has no `--site`
- Interactive `vtc login` is human-only. Agent login requires the user's local `--credentials-file`; never request its contents in chat.
- Credential Markdown fields are `account`, `password`, and optional `totp_secret`. Keep the file outside Git with mode `0600`.
- Never run `vtc myportal apply` or `vtc myportal select`
- Never print passwords, TOTP, cookies, tokens, or storage JSON
- Never copy `*.storage.json` between Mac and the Grok Bot VM
- Never use a proxy browser as a Moodle/MyPortal fallback
- `unverified` means unread, not empty
- PDF/Word/PPT: `sync` then `extract`; paste excerpts, not whole lectures or transcripts
