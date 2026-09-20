# Agent contract

This CLI can be used from Cursor, Codex, Grokbot, or other local agents. Do not mix login with chat.

Login stays on the student's local Terminal. Agents only run read-only `vtc moodle … --json` and `vtc myportal … --json` commands.

Load `.cursor/skills/vtc-cli/SKILL.md` before Moodle/MyPortal/course-file work.

- Binary: `"/Users/ouqixi/vtc cli/.venv/bin/vtc"`
- Moodle `--site ay2526|ay2627` is required; MyPortal has no `--site`
- Never run `vtc login` from an agent
- Never run `vtc myportal apply` or `vtc myportal select`
- Never print passwords, TOTP, cookies, tokens, or storage JSON
- `unverified` means unread, not empty
- PDF/Word/PPT: `sync` then `extract`; paste excerpts, not whole lectures or transcripts
- If there is no session, ask the student to log in on the local Terminal. Do not install the CLI on a remote box, copy session files, or open Moodle/MyPortal through a proxy browser as a fallback.
