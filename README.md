# 個人投影片管理（Local Slide Manager）

這是一個可在 Windows 10/11 直接雙擊啟動的桌面工具，用於：

- 以「白名單資料夾」管理 .pptx 投影片檔
- 文字抽取、建立投影片級索引
- 文字/向量混合搜尋（Hybrid）
- 對話式搜尋（可選：需設定 OpenAI API Key）

重要說明：
- 若您的電腦沒有可用的投影片渲染器（例如 LibreOffice），本工具仍可正常建立文字索引；縮圖會以 placeholder 取代。
- 若未設定 OpenAI API Key，向量功能會使用 fallback_hash 退化模式（可用但品質較差）。

## 一鍵啟動

1. 下載並解壓縮本專案
2. 直接雙擊 `run_app.bat`
3. 啟動後會自動建立 `.venv`、安裝套件並開啟程式

## 使用流程（最常見）

1. 點上方工具列「開啟/建立專案」
   - 選擇一個資料夾作為「專案資料夾」（例如 `D:\MySlidesProject`）
2. 在「檔案庫/索引」分頁左側新增「白名單目錄」
   - 把存放 .pptx 的資料夾加進去
3. 按「掃描檔案」→ 右側會出現檔案清單
4. 按「開始索引（需要者）」
   - 會抽取每頁文字、產生縮圖（若可用）、建立索引
5. 到「搜尋」分頁輸入查詢文字
   - 建議先用 `hybrid`
6. 到「對話」分頁輸入問題
   - 若已在「設定/診斷」設定 OpenAI API Key，會啟用串流回答

## 專案資料夾會產生哪些檔案

- `project.json`：白名單資料夾等專案設定
- `catalog.json`：掃描到的 .pptx 清單與 metadata
- `index.json`：投影片級索引（文字、向量、縮圖路徑）
- `thumbs/`：縮圖輸出（或 placeholder）
- `cache/`：快取（可放 `image_embedder.onnx` 以啟用 ONNX 圖片向量）

## 診斷與選配能力

1. 實際縮圖（選配）
   - 若安裝 LibreOffice，並確保 `soffice` 在 PATH 中，工具會自動嘗試將 pptx 轉成 PNG 縮圖。
2. 圖片向量 ONNX（選配）
   - 將您的 CNN ONNX 模型放到：`<專案資料夾>\cache\image_embedder.onnx`
   - 程式會自動偵測並啟用。
3. OpenAI API Key（建議）
   - 到「設定/診斷」輸入並儲存。
   - 會本機加密保存（不會寫入 logs）。

## 目錄結構

```
.
├─ run_app.bat               # 一鍵啟動（由 project_launcher.py 生成）
├─ requirements.txt          # 依賴（由 project_launcher.py 生成/校驗）
├─ project_launcher.py
├─ README.md
├─ todo.md
├─ src/
│  └─ app/
│     ├─ __main__.py
│     ├─ main.py
│     ├─ core/
│     ├─ services/
│     ├─ ui/
│     └─ utils/
└─ tests/
```

