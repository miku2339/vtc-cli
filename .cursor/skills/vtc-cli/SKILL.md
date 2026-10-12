---
name: vtc-cli
description: Read-only VTC Moodle, MyPortal and Library CLI for courses, files, timetable, catalogue, account, examination papers, syllabuses and public resources. Use for VTC academic, portal or Library reading tasks. Never collect passwords, TOTP, cookies, tokens or Library session data in chat.
---

# VTC CLI

Run from the active repository root with `.venv/bin/vtc`. Resolve its absolute path when running from another directory.

Prefer the absolute path. A missing `vtc` on PATH is not a reason to use a remote box.

## Hard rules

- Agent login requires the user's local `--credentials-file PATH --json`. Interactive login remains human-only.
- Do not print passwords, TOTP seeds, OTP codes, cookies, wstoken, storage JSON, Library JWT, sessionStorage, SAML data, or Authorization headers.
- Moodle `--site ay2526|ay2627` is required. MyPortal and Library have no `--site`.
- Agents are read-only. Do not run `myportal apply` or `myportal select`; do not renew, reserve, cancel, pay fines or book a Library room.
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

## Agent-safe Library commands

Catalogue and official public information do not require a private session:

```bash
.venv/bin/vtc library search --query "design" --scope all --offset 0 --limit 20 --json
.venv/bin/vtc library journals --query "engineering" --json
.venv/bin/vtc library record --id RECORD_ID --json
.venv/bin/vtc library links --json
.venv/bin/vtc library resources --category databases --query "management" --json
.venv/bin/vtc library resources --category videos --query "一帶一路" --json
.venv/bin/vtc library hours --json
.venv/bin/vtc library locations --json
.venv/bin/vtc library news --json
.venv/bin/vtc library services --json
.venv/bin/vtc library regulations --json
.venv/bin/vtc library membership --json
.venv/bin/vtc library remote-access --json
.venv/bin/vtc library room-booking --json
.venv/bin/vtc library laptop-loans --json
.venv/bin/vtc library forms --json
.venv/bin/vtc library staff --json
.venv/bin/vtc library alumni --json
```

Catalogue search scopes are `all`, `catalogue`, `books`, `articles`, `media`, and `videos`. Resource categories are `all`, `databases`, `ebooks`, `ejournals`, `videos`, `repositories`, and `guides`. `links` is the official website directory. For `resources`, inspect `kind`, `scope`, `verified`, and `result_count`: directory entries are not a searched collection, and an unverified response is not an empty result. `room-booking` returns information only and does not create a booking.

The protected services use separate sessions. `catalogue` is the default and is required by account commands; examination papers and syllabuses need their matching service session:

```bash
.venv/bin/vtc library status --service catalogue --json
.venv/bin/vtc library account --json
.venv/bin/vtc library loans --json
.venv/bin/vtc library requests --json
.venv/bin/vtc library fines --json
.venv/bin/vtc library papers --course COURSE_CODE --year YEAR --page 1 --per-page 20 --json
.venv/bin/vtc library syllabuses --course COURSE_CODE --year YEAR --page 1 --per-page 20 --json
.venv/bin/vtc library filters --service papers --json
.venv/bin/vtc library filters --service papers --discipline DISCIPLINE_UUID --department DEPARTMENT_UUID --json
.venv/bin/vtc library download --service papers --id ITEM_ID --query COURSE_CODE --output ./library-files --json
.venv/bin/vtc library extract --path ./library-files/document.pdf --json
```

`papers` and `syllabuses` accept discipline, department, course, year, session, and level filter values returned by `filters`. `--per-page` accepts 10, 20, 50, or 100.

Return names, times, filenames, `source`, and `captured_at`. Prefer excerpt/`key_lines`.

## Login

With a user-prepared private Markdown file on the same machine:

```bash
.venv/bin/vtc login moodle --site ay2627 --credentials-file ~/.config/vtc/credentials.md --json
.venv/bin/vtc login myportal --credentials-file ~/.config/vtc/credentials.md --json
.venv/bin/vtc login library --service catalogue --credentials-file ~/.config/vtc/credentials.md --json
.venv/bin/vtc login library --service papers --credentials-file ~/.config/vtc/credentials.md --json
.venv/bin/vtc login library --service syllabuses --credentials-file ~/.config/vtc/credentials.md --json
```

Fields: `account`, `password`, optional `totp_secret`. Keep the file outside Git with mode `0600`. Never print its contents. Without a credential file, the student runs interactive login in Terminal:

```bash
.venv/bin/vtc login moodle --site ay2526
.venv/bin/vtc login moodle --site ay2627 --store-password
.venv/bin/vtc login myportal --store-password
.venv/bin/vtc login library --service catalogue
.venv/bin/vtc login library --service papers
.venv/bin/vtc login library --service syllabuses
```

Writes that change records are also Terminal-only:

```bash
.venv/bin/vtc myportal apply --id <id> --confirm --json
.venv/bin/vtc myportal select --code COURSE_CODE --confirm --json
```

If a command returns missing session, use the user-prepared local credential file or ask the student to log in locally. Do not retry with env passwords from chat. Do not copy Moodle, MyPortal or Library session material onto Grok Bot's cloud computer, and do not use a proxy browser as a fallback. Grok Bot install/login lives in `GROKBOT.md`.
