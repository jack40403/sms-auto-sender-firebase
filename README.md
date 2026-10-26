# 一安藥局簡訊自動發送系統

Windows 桌面程式，從既有 Excel 寄單工作簿讀取勾選名單，經由摩叮網站填入簡訊。首次安裝須向獨立 Firebase 專案啟用授權。正式發送仍由使用者在程式內明確切換並確認；預設為只填表的模擬模式。

## 安全與資料

- 不要將工作簿、病患資料、傳送日誌、摩叮瀏覽器設定檔、認證或 Firebase Secret Manager 值加入 Git。
- 摩叮密碼保存在 Windows 認證管理員；授權權杖用 Windows DPAPI 加密後保存在 `%LOCALAPPDATA%\SMSAutoSender\sms-auto-sender-firebase-license.json`。
- 安裝版啟動時和掃描／發送前向 Firebase 驗證授權。原始碼開發模式可供本機測試。
- Firebase Firestore 與 Storage 規則均拒絕用戶端直接存取；更新檔須通過授權閘道。
- 已知摩叮登入帳戶可顯示「一安藥局新杏店」，簡訊業務範本仍為「新泰店」。正式發送前請在 UI 再核對。

## 專用 Firebase 專案

專案 ID：`sms-auto-sender-firebase`。本程式不使用 drug price compare 的 Firebase 專案。

此專案已建立 Firestore 與 Storage（`us-central1`），並部署拒絕用戶端直接讀寫的規則。正式授權 API 使用 Cloud Functions；首次部署時需要下列設定，後續更新仍使用同一專案。

1. 在 Firebase 控制台為此專案啟用 Blaze 計費，建立 Firestore 和預設 Storage bucket。後端 Functions 和更新檔儲存需要這些服務。
2. 在 `functions/.env` 設定 `OWNER_APPROVAL_EMAIL=你的管理者信箱`。此檔案不得提交。
3. 使用 Firebase CLI 登入，於專案根目錄執行 `npm --prefix functions ci`。
4. 用 `firebase functions:secrets:set SMTP_APP_PASSWORD --project sms-auto-sender-firebase`、`ACTIVATION_HMAC_SECRET`、`ACTIVATION_BRANCH_PASSWORDS` 設定三項密鑰。HMAC 密鑰請用至少 32 個隨機字元；分店密碼採 JSON 物件，鍵為藥局／分店名稱，值為各分店獨有密碼，至少 8 字元。不要把實際值存入 Git。
5. 執行 `firebase deploy --only functions,firestore:rules,storage --project sms-auto-sender-firebase`。部署後先以測試安裝驗證碼／管理密碼啟用，並從控制台確認 `installations` 紀錄。

`functions/.env.example` 只提供欄位範例。寄驗證碼使用 Gmail SMTP app password，部署時仍須設定此密鑰。程式不會將驗證碼寄給安裝者，只有管理者可從信箱取得。

## 建置安裝包

在 Windows 上安裝 Python 3.13（含 Tk）、[Inno Setup 6](https://jrsoftware.org/isdl.php)，然後：

```powershell
py -3.13 -m venv .venv313
.\.venv313\Scripts\python.exe -m pip install -r requirements-build.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_installer.ps1
```

產物是 `release\SMSAutoSender-Setup-1.0.0.exe`。更新版本時，先同步修改 `app_meta.json` 的 `version` 和 `installer.iss` 的 `AppVersion`，建置腳本會檢查兩者一致；`sms_app_meta.py` 由 `app_meta.json` 讀取版本。安裝器目前未簽署程式碼；Windows 可能提示未知發行者。

## 發布後續更新

先完成安裝包建置，再安裝 `requirements-publish.txt`。可使用已登入的 Firebase CLI，或具備此 bucket 寫入權限的 Google Application Default Credentials：

```powershell
.\.venv313\Scripts\python.exe -m pip install -r requirements-publish.txt
.\.venv313\Scripts\python.exe .\scripts\publish_update.py --dry-run
firebase login
.\.venv313\Scripts\python.exe .\scripts\publish_update.py --auth firebase-cli
```

使用 ADC 時省略 `--auth firebase-cli`。若系統 Node 無法執行 Firebase CLI，可在目前的 PowerShell 視窗將 `SMS_NODE_EXE` 設為可用的 `node.exe` 路徑。

發布腳本先驗證版本高於遠端版本、上傳安裝器，再上傳 `manifest.json`；最後讀回 manifest 核對。授權安裝版在程式內點「檢查更新」，會驗證下載檔案的大小及 SHA-256，再由使用者確認安裝。此流程與 GitHub 原始碼發布分開；安裝包不包含工作簿、病患資料或本機授權檔。

## 使用

請先看 [操作說明.md](操作說明.md)。首次啟動安裝版，輸入藥局／分店名稱後以管理密碼或六位數管理者驗證碼啟用。日常操作維持先以模擬模式核對網頁資料，再考慮正式發送。

本機 `.secret.local` 保存此專案新建立的「一安藥局新泰店」管理密碼；它已從 Git 排除，不隨安裝包發佈。請自行妥善保存，並勿傳給安裝使用者以外的人。
