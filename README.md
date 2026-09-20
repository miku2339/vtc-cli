# vtc-cli

非官方的 VTC Moodle / MyPortal 命令列工具。用你自己的學生帳戶在**本機 Terminal**登入；密碼、TOTP、cookie、token 不會進 git，也不該貼到聊天裡。

這不是 VTC 官方產品，也不是雲端 MCP。帳號與 session 必須留在你的電腦。

## 安裝

需要 Python 3.11+（macOS 可用 Keychain 存密碼）。

```bash
git clone https://github.com/miku233333/vtc-cli.git
cd vtc-cli
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m playwright install chromium
```

之後指令都是 `vtc`。若沒啟動 venv，用 `.venv/bin/vtc`。

## 登入（只在本機 Terminal）

Moodle 必須指定學年，沒有預設值。MyPortal 沒有 `--site`。

```bash
vtc login moodle --site ay2526
vtc login moodle --site ay2627
vtc login myportal
```

可選：`--store-password` 把密碼寫進 macOS Keychain。`--totp-auto` 只用於 Moodle，而且只有頁面真的出現驗證碼時才會讀 TOTP seed。

不要在 Cursor / 聊天裡跑 `vtc login`。stdin 不是 TTY、又沒有 Keychain 或短命環境變數時，登入會直接拒絕，不會把密碼印出來。

## Moodle

```bash
vtc moodle status --site ay2627 --json
vtc moodle courses --site ay2627 --json
vtc moodle assignments --site ay2627 --course COURSE_CODE --json
vtc moodle sync --site ay2627 --course COURSE_CODE --output ./COURSE_CODE --dry-run --json
vtc moodle extract --path ./COURSE_CODE/lecture.pdf --json
```

`sync` 會下載課程裡的 File / Folder / Assignment 附件（PDF、`.docx`、`.pptx` 等），並在本機讀文字。JSON 只保留摘錄；完整文字寫在檔案旁邊的 `*.extracted.txt`。

## MyPortal

```bash
vtc myportal status --json
vtc myportal timetable --today --json
vtc myportal activities --json
vtc myportal modules --json
vtc myportal transcript --json
vtc myportal tuition --json
```

成績單／學費單走「文件下載」（學業成績證明書、學費繳費通知書）。加 `--output DIR` 可把 PDF 存下來。讀不到是 `unverified`，不是「沒有課／沒有帳單」；那些檔案本來也只會留一段時間。

會改紀錄的動作只准本機 Terminal，而且必須加 `--confirm`：

```bash
vtc myportal apply --id <活動id> --confirm
vtc myportal select --code COURSE_CODE --confirm
```

Session 存在 `~/.local/share/vtc/`，權限 `0600`。不要把 `*.storage.json` 傳給別人。

## Cursor / 其他 agent

Repo 內有 `.cursor/skills/vtc-cli/SKILL.md`。Agent 只可跑只讀的 `vtc moodle … --json` 與 `vtc myportal … --json`，不可跑 `login`、`apply`、`select`。

## 安全

- 不要把密碼、TOTP、cookie、token、session 檔貼到 issue、PR 或群組。
- 每個同學用自己的 CNA 登入。
- `unverified` 代表還沒讀到，不代表沒有作業、課表或帳單。

## 授權

MIT。VTC、Moodle、MyPortal 是其權利人的名稱；本工具未獲官方背書。
