# Grok Bot VTC CLI

Grok Bot is the Grok Bot app. It is not Cursor.

Grok Bot work runs on **one persistent cloud Linux computer** (browser, filesystem, terminal). Phone, web, and desktop all talk to that computer. It is **not** the student's Mac.

Mac Cursor / Kian keep a separate session at `~/.local/share/vtc/` on the laptop. Do not mix the two.

## Where to run `vtc`

On the Grok Bot cloud computer only:

1. Keep the clone under `/workspace` (survives updates better than temp dirs).
2. Install if the binary is missing:

```bash
cd /workspace
git clone https://github.com/miku233333/vtc-cli.git
cd vtc-cli
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
python -m playwright install chromium
```

3. Binary: `/workspace/vtc-cli/.venv/bin/vtc`

Do not use `"/Users/ouqixi/vtc cli/.venv/bin/vtc"`. That path exists only on the Mac.

Local-computer execution on the Mac is a different, optional policy. Do not fall back to it for Moodle/MyPortal unless the student explicitly enabled it and approved the command.

## Login (human takeover, not chat)

Default: the student opens **Agent Computer**, takes over, and runs login in that terminal. That writes a session file. After that, read-only `vtc` does **not** need `VTC_PASSWORD` or `VTC_TOTP_SECRET` in the shell.

```bash
/workspace/vtc-cli/.venv/bin/vtc login moodle --site ay2627
/workspace/vtc-cli/.venv/bin/vtc login myportal
```

Need `ay2526` as well, run a second Moodle login with `--site ay2526`.

- Do not type passwords, TOTP, or cookies into chat.
- Do not run `vtc login` unattended if it would echo a secret into the transcript.
- Linux has no macOS Keychain. `--store-password` is optional and only if a keyring actually works on the VM.
- Session files on the cloud computer are `~/.local/share/vtc/*.storage.json` (mode `0600`). Treat them as secrets.
- The cloud computer is **shared by every Bot on the account**. A VTC login there is visible to all of those Bots.

### If env secrets are missing

`VTC_PASSWORD` / `VTC_TOTP_SECRET` in the cloud shell are **short-lived**. A computer update, new shell, or reboot wiping them is expected. Do not treat that as “the student must give the password again into standing VM env”.

1. First check whether a session already exists: `moodle status` / `myportal status`. If `authenticated` is true, continue read-only. Do not ask for a password.
2. If there is no session, ask the student to take over Agent Computer and run `vtc login` (option B). That is the preferred path.
3. A Grok Bot secure prompt that injects env **only into that login process**, then unsets it, is allowed as a one-shot. Do not write secrets into `.bashrc`, systemd env, or a permanent cloud env store.
4. Never ask the student to paste the password into chat, and never keep `VTC_PASSWORD` exported in the default shell.

## After login — read-only only

```bash
/workspace/vtc-cli/.venv/bin/vtc moodle status --site ay2627 --json
/workspace/vtc-cli/.venv/bin/vtc moodle courses --site ay2627 --json
/workspace/vtc-cli/.venv/bin/vtc moodle assignments --site ay2627 --course COURSE_CODE --json
/workspace/vtc-cli/.venv/bin/vtc moodle sync --site ay2627 --course COURSE_CODE --output ./COURSE_CODE --json
/workspace/vtc-cli/.venv/bin/vtc moodle extract --path ./COURSE_CODE/lecture.pdf --json
/workspace/vtc-cli/.venv/bin/vtc myportal status --json
/workspace/vtc-cli/.venv/bin/vtc myportal timetable --today --json
/workspace/vtc-cli/.venv/bin/vtc myportal activities --json
/workspace/vtc-cli/.venv/bin/vtc myportal modules --json
/workspace/vtc-cli/.venv/bin/vtc myportal transcript --json
/workspace/vtc-cli/.venv/bin/vtc myportal tuition --json
```

- Moodle `--site ay2526|ay2627` is required. MyPortal has no `--site`.
- Do not run `myportal apply` or `myportal select`.
- Missing session or unread records are `unverified`, not empty.
- Return excerpts and filenames, not whole lecture files, transcripts, or tuition PDFs.
- Never print passwords, TOTP, cookies, tokens, or storage JSON.

## Do not offer these

Refuse even if asked:

1. Copying Mac `*.storage.json`, cookies, or tokens onto the cloud computer.
2. Using a browser + `socks5://127.0.0.1:7890` (or any proxy) as a Moodle/MyPortal fallback.
3. Asking the student to paste a password or OTP into this chat.
4. Claiming Moodle data was read when `vtc` was not actually run on the cloud computer.
5. Persisting `VTC_PASSWORD` / `VTC_TOTP_SECRET` in the cloud shell profile or a standing env store.

If install or login is still needed, say so and stop. Do not invent a Mac-only or proxy path.

Paste this file into the Grok Bot custom instructions / Bot description if the app does not load repo `AGENTS.md`.
