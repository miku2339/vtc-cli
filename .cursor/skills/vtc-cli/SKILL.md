---
name: vtc-cli
description: Read-only VTC Moodle and MyPortal CLI for courses, files, timetable, activities, modules, transcript, and tuition notices. Use when the user mentions VTC, Moodle, MyPortal, lecture files, deadlines, timetable, module selection, activity enrolment, transcripts, or tuition. Never collect passwords, TOTP, cookies, or tokens in chat.
---

# VTC CLI

Run from the active repository root with `.venv/bin/vtc`. Resolve its absolute path when running from another directory.

Prefer the absolute path. A missing `vtc` on PATH is not a reason to use a remote box.

## Hard rules

- Agent login requires the user's local `--credentials-file PATH --json`. Interactive login remains human-only.
- Do not print passwords, TOTP seeds, OTP codes, cookies, wstoken, storage JSON, or Authorization headers.
- Moodle `--site ay2526|ay2627` is required. MyPortal has no `--site`.
- Agents are read-only. Do not run `myportal apply` or `myportal select`.
- Missing session or unread records are `unverified`, not "no homework / no class / no bill".
- Keep academic details private. Do not paste whole lecture files, transcripts, or tuition PDFs into chat.

## Agent-safe Moodle commands

```bash
.venv/bin/vtc moodle status --site ay2627 --json
.venv/bin/vtc moodle courses --site ay2627 --json
.venv/bin/vtc moodle assignments --site ay2627 --course COURSE_CODE --json
.venv/bin/vtc moodle sync --site ay2627 --course COURSE_CODE --output ./COURSE_CODE --json
.venv/bin/vtc moodle extract --path ./COURSE_CODE/lecture.pdf --json
```

## Agent-safe MyPortal commands

```bash
.venv/bin/vtc myportal status --json
.venv/bin/vtc myportal timetable --json
.venv/bin/vtc myportal timetable --today --json
.venv/bin/vtc myportal activities --json
.venv/bin/vtc myportal modules --json
.venv/bin/vtc myportal transcript --json
.venv/bin/vtc myportal tuition --json
```

Return names, times, filenames, `source`, and `captured_at`. Prefer excerpt/`key_lines`.

## Login

With a user-prepared private Markdown file on the same machine:

```bash
.venv/bin/vtc login moodle --site ay2627 --credentials-file ~/.config/vtc/credentials.md --json
.venv/bin/vtc login myportal --credentials-file ~/.config/vtc/credentials.md --json
```

Fields: `account`, `password`, optional `totp_secret`. Keep the file outside Git with mode `0600`. Never print its contents. Without a credential file, the student runs interactive login in Terminal:

```bash
.venv/bin/vtc login moodle --site ay2526
.venv/bin/vtc login moodle --site ay2627 --store-password
.venv/bin/vtc login myportal --store-password
```

Writes that change records are also Terminal-only:

```bash
.venv/bin/vtc myportal apply --id <id> --confirm --json
.venv/bin/vtc myportal select --code COURSE_CODE --confirm --json
```

If a command returns missing session, use the user-prepared local credential file or ask the student to log in locally. Do not retry with env passwords from chat. Do not copy `*.storage.json` onto Grok Bot's cloud computer, and do not use a proxy browser as a Moodle/MyPortal fallback. Grok Bot install/login lives in `GROKBOT.md`.
