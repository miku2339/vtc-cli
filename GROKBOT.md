# Grokbot VTC CLI

Grokbot is the Grok Bot app. It is not Cursor.

Use the same read-only VTC CLI as other local agents.

Canonical binary on this Mac:

`"/Users/ouqixi/vtc cli/.venv/bin/vtc"`

This Mac also has `~/bin/vtc` on PATH. Prefer the absolute path in agents so a missing PATH is not treated as “not on this Mac”.

## Rules

- Do not run `vtc login`. The student logs in from a local Mac Terminal (TTY).
- Session lives on that Mac: `~/.local/share/vtc/` plus optional Keychain. Treat session files as secrets.
- Agents may run read-only commands on the **same Mac** that already has a session (for example via machineId). That is not “running without the Mac”.
- Do not print passwords, TOTP, cookies, tokens, or storage JSON.
- Moodle `--site ay2526` or `--site ay2627` is required. MyPortal has no `--site`.
- Allowed Moodle: `status`, `courses`, `assignments`, `sync`, `extract` with `--json`.
- Allowed MyPortal: `status`, `timetable`, `activities`, `modules`, `transcript`, `tuition` with `--json`.
- Do not run `myportal apply` or `myportal select`.
- Missing session or unread records are `unverified`, not empty.
- Return excerpts and filenames, not whole lecture files, transcripts, or tuition PDFs.

## Agent-safe commands

```bash
"/Users/ouqixi/vtc cli/.venv/bin/vtc" moodle status --site ay2627 --json
"/Users/ouqixi/vtc cli/.venv/bin/vtc" moodle courses --site ay2627 --json
"/Users/ouqixi/vtc cli/.venv/bin/vtc" myportal status --json
"/Users/ouqixi/vtc cli/.venv/bin/vtc" myportal timetable --today --json
```

## If there is no session

Ask the student to open a local Terminal and run:

```bash
"/Users/ouqixi/vtc cli/.venv/bin/vtc" login moodle --site ay2627 --store-password
"/Users/ouqixi/vtc cli/.venv/bin/vtc" login myportal --store-password
```

Then retry the read-only commands on this Mac. Do not retry with passwords from chat.

If status returns `unverified` / missing session, say that. Do not switch to a box, a proxy browser, or a copied session.

## Do not offer these

Do not suggest “完全唔靠 Mac”. Refuse these even if asked:

1. Installing the CLI on a remote box as a substitute for the Mac session.
2. Copying `*.storage.json`, cookies, or tokens from Mac to a box.
3. Using a browser + `socks5://127.0.0.1:7890` (or any proxy) as a Moodle/MyPortal fallback for this CLI.

If the student wants Moodle/MyPortal data, the path is: local Terminal login, then Mac read-only `vtc … --json` using the canonical binary above.

Paste this file into Grok Bot custom instructions if the app does not load repo `AGENTS.md`.
