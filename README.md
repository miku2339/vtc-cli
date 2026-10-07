# vtc-cli

非官方 VTC Moodle／MyPortal 命令列工具。登入與 session 留在執行指令的電腦，可用 JSON 輸出供本機工具或 agent 讀取。

功能包括：

- Moodle 課程、功課與截止日期
- 課程檔案同步及 PDF／Word／PowerPoint 文字擷取
- MyPortal 課表、活動報名選項、選科資料
- 成績與學費文件查閱及下載

## 安裝

需要 Python 3.11+ 及 Playwright Chromium。先進入專案目錄，再建立及啟用虛擬環境：

```bash
git clone https://github.com/miku233333/vtc-cli.git
cd vtc-cli
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m playwright install chromium
```

之後可使用 `vtc`。每次開新 Terminal 都要先 `cd` 到專案並啟用 `.venv`；或直接使用 `.venv/bin/vtc`。

## 登入

Moodle 的所有指令都必須指定學年：`--site ay2526` 或 `--site ay2627`。MyPortal 沒有 `--site`。

一般情況由帳戶持有人在本機 Terminal 互動登入：

```bash
vtc login moodle --site ay2627
vtc login myportal
```

可加 `--headed` 顯示瀏覽器。`--store-password` 會把密碼保存到系統 Keychain。Moodle 的 `--totp-auto` 可在頁面要求 MFA 時使用已保存的 TOTP；`--store-totp` 可配合它保存 TOTP seed。

### 私人 Markdown 帳密檔

帳戶持有人亦可明確授權自動登入，並在專案外準備私人 Markdown 檔：

```text
account: ACCOUNT_PLACEHOLDER
password: PASSWORD_PLACEHOLDER
totp_secret: BASE32_OR_OTPAUTH_URI_PLACEHOLDER
```

`account` 與 `password` 必填。`totp_secret` 選填，可使用 Base32 seed 或 `otpauth://` URI。檔案是明文敏感資料；工具讀取時會把權限設為 `0600`，但仍應存放在專案及同步資料夾之外。

```bash
vtc login moodle --site ay2627 --credentials-file ~/secure/credentials.md --json
vtc login myportal --credentials-file ~/secure/credentials.md --json
```

只有帳戶持有人已明確授權並自行準備此檔案時，agent 才可使用 `--credentials-file`。不要把帳密內容貼到聊天、Issue、PR 或指令輸出。

## Moodle 常用指令

```bash
vtc moodle status --site ay2627 --json
vtc moodle courses --site ay2627 --json
vtc moodle assignments --site ay2627 --course COURSE_CODE --json
vtc moodle sync --site ay2627 --course COURSE_CODE --output ./course-files --dry-run --json
vtc moodle sync --site ay2627 --course COURSE_CODE --output ./course-files --json
vtc moodle extract --path ./course-files/lecture.pdf --json
```

`sync` 會下載課程 File、Folder 及 Assignment 附件，並預設擷取支援檔案的文字；加 `--no-extract` 可只下載。`--dry-run` 只列出計劃，不寫入下載檔。`extract` 支援已下載的 PDF、Word 及 PowerPoint 檔案。

## MyPortal 常用指令

```bash
vtc myportal status --json
vtc myportal timetable --json
vtc myportal timetable --today --json
vtc myportal activities --json
vtc myportal modules --json
vtc myportal transcript --json
vtc myportal transcript --output ./documents --json
vtc myportal tuition --json
vtc myportal tuition --output ./documents --json
```

活動報名及選科會改動校方紀錄，只供帳戶持有人在互動式本機 Terminal 使用，並必須明確加入 `--confirm`：

```bash
vtc myportal apply --id ACTIVITY_ID --confirm
vtc myportal select --code MODULE_CODE --confirm
```

## Agent 使用邊界

在帳戶持有人完成登入，或明確授權私人帳密檔登入後，agent 可執行只讀的 Moodle／MyPortal 指令及獲授權的本機下載、擷取工作。Agent 不可執行 `myportal apply` 或 `myportal select`，亦不可提交表單、改動校方紀錄或公開敏感資料。

`unverified` 表示工具未能可靠讀取或確認資料，不代表「沒有功課」、「沒有課堂」、「沒有選科」或「沒有文件」。自動化程式應同時檢查 JSON 的 `ok`、`status`、`source` 及程序 exit code。

| Exit code | 意義 |
|---:|---|
| `0` | 已驗證成功 |
| `1` | 執行或內部錯誤 |
| `2` | `unverified`、缺少可用 session；無效命令列參數亦可能由 argparse 使用此代碼 |
| `3` | 工具判定的用法錯誤 |
| `4` | 登入失敗、敏感資料輸入被拒或寫入操作被阻擋 |

## 私密資料

- Session 預設保存在 `~/.local/share/vtc/`，檔案權限為 `0600`。
- `*.storage.json`、帳密 Markdown、密碼、TOTP、cookie、token 及下載表單 hidden fields 都屬敏感資料。
- 不要提交上述資料到 Git；`.gitignore` 只涵蓋常見檔名，私人檔仍應放在專案外。
- 不要在不同電腦、agent VM 或不同使用者之間複製 session 檔。每部電腦都應由同一帳戶持有人在該機器登入。
- `--json` 會過濾已知敏感欄位，但不應視為保存或分享完整輸出的授權。

## 授權與聲明

本專案採用 MIT License。VTC、Moodle 與 MyPortal 名稱及服務屬其各自權利人；本工具並非官方產品，亦未獲官方背書。
