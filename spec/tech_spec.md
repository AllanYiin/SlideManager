# Vibe Coding 開發規範

|## 專業技術版（Tech Spec）

### 0. 技術可行性分析

**可行：**

* 以「目錄白名單」掃描本機 `.pptx` 並產生清單：可行（標準檔案系統操作）。
* 讀取 PPTX 文字與 metadata：可行（`python-pptx` 可讀 core properties、shape text）。
* 文字向量化：可行（OpenAI Embeddings API）。
* 縮圖 → 2048 向量：可行（`onnxruntime` + 自訂 CNN ONNX）。
* 小規模（數千筆）混合搜尋（BM25 + 向量）：可行（BM25 用 `rank-bm25` 或簡易實作；向量用 brute-force cosine 即可）。

**風險/限制（需在規格中明確）：**

* **PPTX 產生縮圖**：`python-pptx` 不能渲染投影片成圖片；需外部渲染器：

  * 跨平台建議：**LibreOffice headless**（本機安裝依賴）匯出每頁 PNG。
  * Windows-only 替代：PowerPoint COM（需 Office）。
  * 需做成「可插拔 Renderer」；找不到渲染器時仍可索引文字、但無法搜圖/預覽縮圖。
* 向量存 JSON：列表型 JSON 會膨脹；仍可行（數千筆），但建議提供「壓縮序列化（base64 bytes）」選項以減小檔案。
* OpenAI API：需金鑰、配額與 rate limit；索引需可暫停/續跑、重試與快取避免重複付費。

---

### 1. 背景與目標

**背景**：本機有大量投影片（數百～上千檔），需要能快速知道「某張投影片（某一頁）在哪個檔案裡」。
**目標**：

1. 依白名單目錄列出所有 PPTX，顯示名稱、metadata、索引狀態。
2. 只對「未索引」或「檔案修改日晚於索引日」的投影片進行（重）索引。
3. 索引每頁：抽文字、產縮圖、文字 embedding、縮圖 embedding（2048）、並支援：

   * **搜文字**、**搜圖**、**搜整體（concat 向量）**
   * **混合搜尋（BM25 + 向量）**
4. UI：tabbed UI、本機 Python 應用，長任務 **不可 lock UI**，結果要視覺化，支援「對話式搜尋」。
5. 小規模：數據千筆級，持久化建議 JSON，開啟自動載入，避免受 numpy 2.x 存檔格式影響（因此避免把資料綁死在 `.npy`）。

---

### 2. 範圍（Scope）

**In Scope**

* 目錄白名單管理（新增/移除/啟用停用）
* 掃描 PPTX 清單 + metadata
* 索引策略（新增/更新/刪除一致性）
* 文字抽取（標題+內文）、縮圖生成、向量生成、混合搜尋
* 投影片內容預覽（不需編輯）
* JSON 持久化（專案檔）

**Out of Scope（先不做）**

* 多使用者權限、登入
* 網路同步/雲端
* 大規模 ANN（HNSW/FAISS）必要性（可留擴充點）
* 投影片編輯

---

### 3. 角色與使用情境

**角色**

* 使用者：在本機管理投影片、需要找「某頁在哪」的人
* 系統：本機 App（UI + 索引器 + 搜尋引擎）

**主要情境**

1. 啟動 App → 自動載入專案 → 顯示 PPTX 清單與索引狀態
2. 使用者新增白名單資料夾 → 重新掃描 → 清單更新
3. 使用者點「建立索引」→ 系統只索引需要的檔案 → 完成後可搜尋
4. 使用者輸入文字查詢 → 顯示命中投影片（縮圖網格 + 排名 + 高亮文字）
5. 使用者丟一張圖片（或截圖）→ 以縮圖向量搜相似頁
6. 使用者使用對話框（LLM）描述「我要找……那張投影片」→ 系統解析查詢策略（文字/混合/篩選）→ 回傳結果並可追問

---

### 4. 系統模組設計

#### 4.1 模組分層

* **UI Layer**

  * Library/Index Tab（清單、狀態、索引控制）
  * Search Tab（文字/圖片/混合搜尋、視覺化）
  * Chat Tab（對話式搜尋，Streaming）
  * Settings/Diagnostics Tab（白名單、金鑰、渲染器狀態、模型版本、索引路徑、日誌）

* **Domain Layer**

  * CatalogService（掃描清單、metadata）
  * IndexService（決策哪些要索引、排程、續跑、取消）
  * ExtractionService（文字抽取）
  * RenderService（投影片 → 縮圖，Renderer 可插拔）
  * EmbeddingService（文字 embeddings、圖片 embeddings）
  * SearchService（BM25、向量相似度、混合融合、排序、過濾）
  * ProjectStore（JSON 持久化、版本遷移）

* **Infra Layer**

  * FileSystemAdapter（掃描、mtime、hash）
  * OpenAIClient（embeddings、chat streaming）
  * OnnxRuntimeAdapter（模型下載/載入/推論）
  * Tokenizer（BM25 用）
  * Logger

#### 4.2 前後端模組架構圖（SVG）

```svg
<svg width="980" height="520" viewBox="0 0 980 520" xmlns="http://www.w3.org/2000/svg">
  <rect x="20" y="20" width="940" height="480" rx="16" fill="#F8FAFC" stroke="#CBD5E1"/>
  <text x="40" y="55" font-size="20" font-family="Arial" fill="#0F172A">Local PPTX Slide Manager (Python Desktop App)</text>

  @-- UI --
  <rect x="40" y="90" width="260" height="380" rx="14" fill="#FFFFFF" stroke="#CBD5E1"/>
  <text x="60" y="120" font-size="16" font-family="Arial" fill="#0F172A">UI Layer (Tabbed)</text>
  <rect x="60" y="140" width="220" height="44" rx="10" fill="#EEF2FF" stroke="#C7D2FE"/>
  <text x="74" y="168" font-size="13" font-family="Arial" fill="#1E293B">Library/Index Tab</text>
  <rect x="60" y="192" width="220" height="44" rx="10" fill="#EEF2FF" stroke="#C7D2FE"/>
  <text x="74" y="220" font-size="13" font-family="Arial" fill="#1E293B">Search Tab</text>
  <rect x="60" y="244" width="220" height="44" rx="10" fill="#EEF2FF" stroke="#C7D2FE"/>
  <text x="74" y="272" font-size="13" font-family="Arial" fill="#1E293B">Chat Tab (Streaming)</text>
  <rect x="60" y="296" width="220" height="44" rx="10" fill="#EEF2FF" stroke="#C7D2FE"/>
  <text x="74" y="324" font-size="13" font-family="Arial" fill="#1E293B">Settings/Diagnostics</text>
  <text x="60" y="370" font-size="12" font-family="Arial" fill="#64748B">Non-blocking: Background workers</text>

  @-- Services --
  <rect x="330" y="90" width="330" height="380" rx="14" fill="#FFFFFF" stroke="#CBD5E1"/>
  <text x="350" y="120" font-size="16" font-family="Arial" fill="#0F172A">Domain Services</text>

  <rect x="350" y="140" width="290" height="44" rx="10" fill="#ECFEFF" stroke="#A5F3FC"/>
  <text x="364" y="168" font-size="13" font-family="Arial" fill="#1E293B">CatalogService (scan + metadata)</text>

  <rect x="350" y="192" width="290" height="44" rx="10" fill="#ECFEFF" stroke="#A5F3FC"/>
  <text x="364" y="220" font-size="13" font-family="Arial" fill="#1E293B">IndexService (queue/cancel/resume)</text>

  <rect x="350" y="244" width="290" height="44" rx="10" fill="#ECFEFF" stroke="#A5F3FC"/>
  <text x="364" y="272" font-size="13" font-family="Arial" fill="#1E293B">Extraction + Render + Embedding</text>

  <rect x="350" y="296" width="290" height="44" rx="10" fill="#ECFEFF" stroke="#A5F3FC"/>
  <text x="364" y="324" font-size="13" font-family="Arial" fill="#1E293B">SearchService (BM25 + Vector + Hybrid)</text>

  <rect x="350" y="348" width="290" height="44" rx="10" fill="#ECFEFF" stroke="#A5F3FC"/>
  <text x="364" y="376" font-size="13" font-family="Arial" fill="#1E293B">ProjectStore (JSON + migration)</text>

  @-- Infra --
  <rect x="680" y="90" width="260" height="380" rx="14" fill="#FFFFFF" stroke="#CBD5E1"/>
  <text x="700" y="120" font-size="16" font-family="Arial" fill="#0F172A">Infra / Adapters</text>

  <rect x="700" y="140" width="220" height="44" rx="10" fill="#FFF7ED" stroke="#FDBA74"/>
  <text x="714" y="168" font-size="13" font-family="Arial" fill="#1E293B">FileSystemAdapter</text>

  <rect x="700" y="192" width="220" height="44" rx="10" fill="#FFF7ED" stroke="#FDBA74"/>
  <text x="714" y="220" font-size="13" font-family="Arial" fill="#1E293B">Renderer (LO/COM plugin)</text>

  <rect x="700" y="244" width="220" height="44" rx="10" fill="#FFF7ED" stroke="#FDBA74"/>
  <text x="714" y="272" font-size="13" font-family="Arial" fill="#1E293B">OpenAIClient (Embeddings/Chat)</text>

  <rect x="700" y="296" width="220" height="44" rx="10" fill="#FFF7ED" stroke="#FDBA74"/>
  <text x="714" y="324" font-size="13" font-family="Arial" fill="#1E293B">OnnxRuntimeAdapter (2048-d)</text>

  <rect x="700" y="348" width="220" height="44" rx="10" fill="#FFF7ED" stroke="#FDBA74"/>
  <text x="714" y="376" font-size="13" font-family="Arial" fill="#1E293B">Logger + Tokenizer</text>

  @-- arrows --
  <line x1="300" y1="200" x2="330" y2="200" stroke="#94A3B8" stroke-width="2"/>
  <line x1="660" y1="260" x2="680" y2="260" stroke="#94A3B8" stroke-width="2"/>
</svg>
```

---

### 5. 資料模型與持久化（JSON）

> 原則：**可續跑**、**可版本遷移**、**新增必有刪/改流程**、**不依賴 numpy 檔案格式**。

#### 5.1 專案目錄結構（建議）

* `{project_root}/`

  * `project.json`（全域設定 + 版本）
  * `catalog.json`（檔案清單 + metadata + 索引狀態）
  * `index.json`（slide-level 索引：文字、向量、thumbnail 路徑、token）
  * `thumbs/`（縮圖檔案，`{file_id}/{slide_id}.png`）
  * `cache/`（OpenAI 回應快取、ONNX 模型檔）

#### 5.2 Catalog（檔案層）

```json
{
  "schema_version": "1.0",
  "whitelist_dirs": [
    {"path": "/Users/me/slides", "enabled": true, "recursive": true}
  ],
  "files": [
    {
      "file_id": "sha1(normalized_path)",
      "path": "/Users/me/slides/A.pptx",
      "filename": "A.pptx",
      "size_bytes": 1234567,
      "mtime_epoch": 1730000000,
      "slide_count": 42,
      "core_properties": {
        "title": "Q3 Review",
        "author": "Amy",
        "created_epoch": 1729000000,
        "modified_epoch": 1730000000
      },
      "index_status": {
        "indexed": true,
        "indexed_epoch": 1730001000,
        "index_mtime_epoch": 1730000000,
        "index_slide_count": 42,
        "last_error": null
      }
    }
  ]
}
```

#### 5.3 Index（投影片頁層）

> 每頁同時保存：`title_text`、`body_text`、`bm25_tokens`、`text_embedding`、`image_embedding(2048)`、`concat_embedding`、`thumb_path`

向量存法（兩種擇一）：

* **A. list-of-floats**（最直覺、檔案較大）
* **B. base64 bytes（float32 little-endian）**（較小、較快、仍是 JSON）

```json
{
  "schema_version": "1.0",
  "embedding": {
    "text_model": "text-embedding-3-large",
    "text_dim": 3072,
    "image_dim": 2048,
    "concat_dim": 5120,
    "vector_encoding": "base64_f32"
  },
  "slides": [
    {
      "slide_uid": "file_id#slide_index",
      "file_id": "....",
      "slide_index": 12,
      "title_text": "Market Overview",
      "body_text": "....",
      "all_text": "Market Overview\n....",
      "bm25_tokens": ["market","overview","..."],
      "thumb_path": "thumbs/<file_id>/12.png",
      "text_vec": "BASE64...",
      "image_vec": "BASE64...",
      "concat_vec": "BASE64...",
      "indexed_epoch": 1730001200
    }
  ]
}
```

---

### 6. 核心流程

#### 6.1 掃描清單（CatalogService）

1. 讀取 `project.json` → whitelist_dirs
2. 遍歷目錄（enabled + recursive）找 `.pptx`
3. 對每個檔案取得：path、size、mtime
4. 若檔案新出現 → 建立 file entry
5. 若原有 entry path 不存在 → 標記 missing（不立刻刪，給 UI 做清理）
6. 讀取 metadata（`python-pptx` core properties、slide_count）→ 背景執行、可中斷

#### 6.2 索引決策（IndexService）

符合任一條件就排入索引：

* `indexed=false`
* 或 `file.mtime_epoch > file.index_status.index_mtime_epoch`
* 或 `slide_count` 變動（可能新增/刪除頁）

索引完成後更新：

* `indexed=true`
* `indexed_epoch=now`
* `index_mtime_epoch=file.mtime_epoch`
* `index_slide_count=slide_count`

#### 6.3 建立索引（Extraction + Render + Embedding）

對每個待索引 file：

1. **抽取文字**：逐頁讀 shapes 文字（title/內文），合併成 `all_text`
2. **渲染縮圖**：Renderer 產生每頁 PNG（失敗時允許只做文字索引，但需在狀態顯示）
3. **文字向量**：OpenAI embeddings（批次、快取、重試）
4. **圖片向量**：ONNX 2048-d（模型下載 → cache → 載入 → 推論）
5. **向量拼接**：`concat_vec = [text_vec, image_vec]`
6. **BM25 tokens**：對 `all_text` tokenize → 儲存 tokens
7. 寫入 `index.json`（原子寫入：先寫 temp 再 replace）
8. 更新 `catalog.json` 的 index_status

> **Streaming 規範**：Chat Tab 的 LLM 回覆必須 streaming；索引進度也需以「逐步事件」推送 UI（progress events）。

---

### 7. 搜尋設計（BM25 + Vector + Hybrid）

#### 7.1 搜尋模式

* **Text Search**

  * BM25：對 `bm25_tokens` 做 BM25 分數
  * Text Vector：query embedding 與 `text_vec` cosine
* **Image Search**

  * image query → ONNX 2048 → cosine 對 `image_vec`
* **Overall Vector**

  * query concat（text + image）→ cosine 對 `concat_vec`
* **Hybrid（預設）**

  * `score = w_bm25 * norm(bm25) + w_vec * norm(vec_cosine)`
  * 可調權重（UI slider）

#### 7.2 正規化

* BM25 min-max 或 z-score（小數據建議 min-max）
* cosine 轉換成 [0,1]（例如 `(cos+1)/2`）再融合

#### 7.3 結果呈現

每筆結果包含：

* 縮圖、檔名、頁碼、分數、命中片段（title/body 高亮）
* 點擊 → 右側預覽（大圖）+ 全文 + 快速跳到檔案路徑（copy/open folder）

---

### 8. UI/UX 規格（Tabbed UI、不可卡 UI）

#### 8.1 UI 技術建議

* PySide6 / PyQt：背景工作用 `QThread` / `QRunnable` / `QThreadPool`
* 長任務必須支援：

  * 進度條（file-level + slide-level）
  * 暫停 / 續跑 / 取消
  * 錯誤摘要（可複製）

#### 8.2 色彩與風格（淺色）

* 背景：`#F8FAFC`
* 卡片：`#FFFFFF`
* 邊框：`#CBD5E1`
* 主要色（操作按鈕/高亮）：`#2563EB`
* 成功：`#16A34A`
* 警告：`#F59E0B`
* 錯誤：`#DC2626`
* 文字主色：`#0F172A`，次要：`#64748B`

#### 8.3 主畫面示意（SVG：完整 layout + 控制項）

```svg
<svg width="1100" height="650" viewBox="0 0 1100 650" xmlns="http://www.w3.org/2000/svg">
  <rect x="0" y="0" width="1100" height="650" fill="#F8FAFC"/>
  <rect x="20" y="20" width="1060" height="610" rx="18" fill="#FFFFFF" stroke="#CBD5E1"/>

  @-- Top bar --
  <text x="40" y="55" font-size="20" font-family="Arial" fill="#0F172A">Local Slide Manager</text>
  <rect x="820" y="32" width="120" height="34" rx="10" fill="#2563EB"/>
  <text x="845" y="55" font-size="13" font-family="Arial" fill="#FFFFFF">Build Index</text>
  <rect x="950" y="32" width="110" height="34" rx="10" fill="#EEF2FF" stroke="#C7D2FE"/>
  <text x="975" y="55" font-size="13" font-family="Arial" fill="#1E293B">Scan</text>

  @-- Tabs --
  <rect x="40" y="80" width="1020" height="40" rx="12" fill="#F1F5F9" stroke="#CBD5E1"/>
  <rect x="50" y="85" width="170" height="30" rx="10" fill="#2563EB"/>
  <text x="78" y="106" font-size="13" font-family="Arial" fill="#FFFFFF">Library / Index</text>
  <text x="250" y="106" font-size="13" font-family="Arial" fill="#64748B">Search</text>
  <text x="330" y="106" font-size="13" font-family="Arial" fill="#64748B">Chat</text>
  <text x="390" y="106" font-size="13" font-family="Arial" fill="#64748B">Settings</text>

  @-- Left: file list --
  <rect x="40" y="135" width="520" height="475" rx="16" fill="#FFFFFF" stroke="#CBD5E1"/>
  <text x="60" y="165" font-size="14" font-family="Arial" fill="#0F172A">PPTX List (hundreds ~ thousands)</text>
  <rect x="60" y="180" width="480" height="34" rx="10" fill="#F1F5F9" stroke="#CBD5E1"/>
  <text x="75" y="203" font-size="12" font-family="Arial" fill="#64748B">Filter: name / indexed / modified-after-index</text>

  @-- Table header --
  <rect x="60" y="225" width="480" height="28" rx="8" fill="#F8FAFC" stroke="#E2E8F0"/>
  <text x="75" y="244" font-size="12" font-family="Arial" fill="#1E293B">Name</text>
  <text x="300" y="244" font-size="12" font-family="Arial" fill="#1E293B">Metadata</text>
  <text x="455" y="244" font-size="12" font-family="Arial" fill="#1E293B">Indexed</text>

  @-- Rows --
  <rect x="60" y="260" width="480" height="46" rx="10" fill="#FFFFFF" stroke="#E2E8F0"/>
  <text x="75" y="288" font-size="12" font-family="Arial" fill="#0F172A">Q3_review.pptx</text>
  <text x="300" y="288" font-size="12" font-family="Arial" fill="#64748B">42 slides · Amy</text>
  <rect x="465" y="272" width="60" height="22" rx="10" fill="#DCFCE7" stroke="#86EFAC"/>
  <text x="478" y="288" font-size="11" font-family="Arial" fill="#166534">OK</text>

  <rect x="60" y="314" width="480" height="46" rx="10" fill="#FFFFFF" stroke="#E2E8F0"/>
  <text x="75" y="342" font-size="12" font-family="Arial" fill="#0F172A">Pitch_deck.pptx</text>
  <text x="300" y="342" font-size="12" font-family="Arial" fill="#64748B">18 slides · -</text>
  <rect x="445" y="326" width="80" height="22" rx="10" fill="#FEF3C7" stroke="#FCD34D"/>
  <text x="455" y="342" font-size="11" font-family="Arial" fill="#92400E">Needs</text>

  @-- Right: status & preview --
  <rect x="580" y="135" width="480" height="230" rx="16" fill="#FFFFFF" stroke="#CBD5E1"/>
  <text x="600" y="165" font-size="14" font-family="Arial" fill="#0F172A">Index Status</text>
  <rect x="600" y="180" width="440" height="16" rx="8" fill="#E2E8F0"/>
  <rect x="600" y="180" width="220" height="16" rx="8" fill="#2563EB"/>
  <text x="600" y="218" font-size="12" font-family="Arial" fill="#64748B">Progress: file 3/10 · slide 12/42 · ETA hidden</text>
  <rect x="600" y="230" width="110" height="34" rx="10" fill="#EEF2FF" stroke="#C7D2FE"/>
  <text x="628" y="252" font-size="13" font-family="Arial" fill="#1E293B">Pause</text>
  <rect x="720" y="230" width="110" height="34" rx="10" fill="#EEF2FF" stroke="#C7D2FE"/>
  <text x="748" y="252" font-size="13" font-family="Arial" fill="#1E293B">Resume</text>
  <rect x="840" y="230" width="110" height="34" rx="10" fill="#FEE2E2" stroke="#FCA5A5"/>
  <text x="875" y="252" font-size="13" font-family="Arial" fill="#7F1D1D">Cancel</text>

  <rect x="580" y="385" width="480" height="225" rx="16" fill="#FFFFFF" stroke="#CBD5E1"/>
  <text x="600" y="415" font-size="14" font-family="Arial" fill="#0F172A">Slide Preview (read-only)</text>
  <rect x="600" y="430" width="200" height="150" rx="12" fill="#F1F5F9" stroke="#CBD5E1"/>
  <text x="815" y="455" font-size="12" font-family="Arial" fill="#64748B">Title: Market Overview</text>
  <text x="815" y="480" font-size="12" font-family="Arial" fill="#64748B">Body: ...</text>
  <rect x="815" y="520" width="150" height="34" rx="10" fill="#2563EB"/>
  <text x="842" y="542" font-size="13" font-family="Arial" fill="#FFFFFF">Open Folder</text>
</svg>
```

---

### 9. 非功能需求（NFR）

* **UI 不可卡死**：任何 I/O、渲染、embedding、onnx 推論必須背景執行
* **可續跑**：索引中斷（關閉 App、取消、斷網）後可從 JSON 狀態恢復
* **快取**：

  * OpenAI embeddings：以 `hash(all_text + model)` 做快取，避免重複呼叫
  * ONNX 模型檔：下載後 cache，支援版本號
* **原子寫入**：JSON 寫入 temp → replace，避免壞檔
* **可觀測**：log 檔、錯誤摘要、失敗重試次數、最後錯誤原因

---

### 10. 錯誤處理與狀態碼（本機）

以「可讀訊息 + 可重試策略」為主：

* `E_RENDERER_NOT_FOUND`：找不到渲染器 → UI 顯示「只能索引文字；搜圖/預覽不可用」
* `E_PPTX_CORRUPTED`：檔案損壞 → 跳過該檔並標記 error
* `E_OPENAI_RATE_LIMIT`：退避重試（exponential backoff），可暫停續跑
* `E_OPENAI_AUTH`：金鑰錯誤 → 停止文字索引並提示至 Settings
* `E_ONNX_LOAD_FAIL`：模型載入失敗 → 停止圖片索引並提示重新下載
* `E_JSON_CORRUPTED`：專案 JSON 壞掉 → 提供備份回復（建議每次寫入保留 `.bak`）

---

### 11. Edge / Abuse Cases（必列）

* 白名單路徑被移除/無權限 → 掃描跳過並提示
* 同名檔案不同路徑 → 以 `file_id`（hash path）區分
* 檔案被改名/搬移 → 若以 path hash 作 file_id，會視為新檔；可選做「內容 hash」來追蹤（成本較高）
* 索引時檔案被使用者打開並另存 → mtime 變動：當輪索引結束後應再次檢查 mtime，若變更則標記「需再索引」
* 文字抽取為空（只有圖）→ 仍可做圖片索引
* 縮圖生成失敗 → 仍可文字索引，並在結果列表標「無縮圖」
* JSON 過大：提供「只保存 concat_vec」或「base64 encoding」開關
* Chat 對話中連續詢問：必須可取消上一個 streaming 回覆

---

### 12. 驗收條件（Acceptance Criteria）

1. **目錄白名單**

   * 可新增/移除/停用目錄；掃描只讀白名單內檔案
2. **清單顯示**

   * 至少顯示：檔名、路徑、mtime、slide_count、indexed/needs-index/error
3. **索引策略正確**

   * 未索引、或 `mtime > index_mtime` 的檔案會被納入索引
4. **索引內容**

   * 每頁可取得文字（title+body）與縮圖（若 renderer 可用）
   * 文字向量 + 圖片向量 + concat 向量成功寫入專案
5. **搜尋能力**

   * 文字搜尋：BM25 + 向量融合可切換/調權重
   * 圖片搜尋：可用圖片向量找到相似頁
   * 結果可視覺化（縮圖網格/清單），點擊可預覽
6. **不鎖 UI**

   * 掃描、索引、搜尋（含 embedding）期間 UI 可操作、可取消
7. **持久化**

   * 關閉 App 後再開，可讀取 JSON 並恢復索引狀態與可搜尋

---

### 13. 測試案例（含 Gherkin 範例）

#### 13.1 功能測試（精選）

* 掃描：

  * 白名單 2 個目錄，停用其中 1 個 → 清單只出現啟用的
* 索引：

  * 新增 pptx → 顯示 Needs Index → 建索引後變 OK
  * 修改 pptx（mtime 更新）→ 狀態變 Needs → 重建後 OK
* 搜尋：

  * 文字 query 命中某頁標題 → Top 10 應包含該頁
  * 圖片 query 與某頁縮圖相似 → Top 10 應包含該頁
* 不中斷 UI：

  * 索引中可切換 tab、可點 Cancel，且 Cancel 後狀態正確

#### 13.2 Gherkin（示例）

```gherkin
Feature: Indexing only changed PPTX files

  Scenario: Reindex when file modified after last index
    Given the project has indexed "Pitch_deck.pptx" with index_mtime = 1730000000
    And the file "Pitch_deck.pptx" now has mtime = 1730000500
    When the user clicks "Build Index"
    Then the system should enqueue "Pitch_deck.pptx" for reindex
    And after completion, the file status should be "Indexed OK"
```
