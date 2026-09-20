---
name: vtc-cli
description: Read-only VTC Moodle and MyPortal CLI for courses, files, timetable, activities, modules, transcript, and tuition notices. Use when the user mentions VTC, Moodle, MyPortal, lecture files, deadlines, timetable, module selection, activity enrolment, transcripts, or tuition. Never collect passwords, TOTP, cookies, or tokens in chat.
---

# VTC CLI

Canonical binary: `"/Users/ouqixi/vtc cli/.venv/bin/vtc"`
Repo: `/Users/ouqixi/vtc cli`

Prefer the absolute path. A missing `vtc` on PATH is not a reason to use a remote box.

## Hard rules

- Do not run `vtc login`. The student runs it in a local Terminal.
- Do not print passwords, TOTP seeds, OTP codes, cookies, wstoken, storage JSON, or Authorization headers.
- Moodle `--site ay2526|ay2627` is required. MyPortal has no `--site`.
- Agents are read-only. Do not run `myportal apply` or `myportal select`.
- Missing session or unread records are `unverified`, not "no homework / no class / no bill".
- Keep academic details private. Do not paste whole lecture files, transcripts, or tuition PDFs into chat.

## Agent-safe Moodle commands

```bash
"/Users/ouqixi/vtc cli/.venv/bin/vtc" moodle status --site ay2627 --json
"/Users/ouqixi/vtc cli/.venv/bin/vtc" moodle courses --site ay2627 --json
"/Users/ouqixi/vtc cli/.venv/bin/vtc" moodle assignments --site ay2627 --course COURSE_CODE --json
"/Users/ouqixi/vtc cli/.venv/bin/vtc" moodle sync --site ay2627 --course COURSE_CODE --output ./COURSE_CODE --json
"/Users/ouqixi/vtc cli/.venv/bin/vtc" moodle extract --path ./COURSE_CODE/lecture.pdf --json
```

## Agent-safe MyPortal commands

```bash
"/Users/ouqixi/vtc cli/.venv/bin/vtc" myportal status --json
"/Users/ouqixi/vtc cli/.venv/bin/vtc" myportal timetable --json
"/Users/ouqixi/vtc cli/.venv/bin/vtc" myportal timetable --today --json
"/Users/ouqixi/vtc cli/.venv/bin/vtc" myportal activities --json
"/Users/ouqixi/vtc cli/.venv/bin/vtc" myportal modules --json
"/Users/ouqixi/vtc cli/.venv/bin/vtc" myportal transcript --json
"/Users/ouqixi/vtc cli/.venv/bin/vtc" myportal tuition --json
```

Return names, times, filenames, `source`, and `captured_at`. Prefer excerpt/`key_lines`.

## Login (human Terminal only)

```bash
"/Users/ouqixi/vtc cli/.venv/bin/vtc" login moodle --site ay2526
"/Users/ouqixi/vtc cli/.venv/bin/vtc" login moodle --site ay2627 --store-password
"/Users/ouqixi/vtc cli/.venv/bin/vtc" login myportal --store-password
```

Writes that change records are also Terminal-only:

```bash
"/Users/ouqixi/vtc cli/.venv/bin/vtc" myportal apply --id <id> --confirm --json
"/Users/ouqixi/vtc cli/.venv/bin/vtc" myportal select --code COURSE_CODE --confirm --json
```

If a command returns missing session, ask the student to log in locally. Do not retry with env passwords from chat. Do not install this CLI on a remote box, copy `*.storage.json`, or use a proxy browser as a Moodle/MyPortal fallback.
