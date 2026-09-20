# Grokbot VTC CLI

Grokbot is the Grok Bot app. It is not Cursor.

Use the same read-only VTC CLI as other local agents.

Binary: `vtc` (or `.venv/bin/vtc`)

Rules:

- Do not run `vtc login`. The student logs in from a local Terminal.
- Do not print passwords, TOTP, cookies, tokens, or storage JSON.
- Moodle `--site ay2526` or `--site ay2627` is required. MyPortal has no `--site`.
- Allowed Moodle: `status`, `courses`, `assignments`, `sync`, `extract` with `--json`.
- Allowed MyPortal: `status`, `timetable`, `activities`, `modules`, `transcript`, `tuition` with `--json`.
- Do not run `myportal apply` or `myportal select`.
- Missing session or unread records are `unverified`, not empty.
- Return excerpts and filenames, not whole lecture files, transcripts, or tuition PDFs.

Paste this file into Grok Bot custom instructions if the app does not load repo `AGENTS.md`.
