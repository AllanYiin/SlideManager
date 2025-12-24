# 後台重構規格 v2

本文件為後台重構的實作規範與交付物同步版，前端 UI 沿用既有設計，後台獨立 daemon 化，確保可觀測、可取消/暫停、可恢復、可部分成功、以頁為單位、不中斷存檔、絕不無窮等待。

## 1. 設計目標與非功能性要求

### 1.1 核心目標

1. **快**：大量投影片本地端批次處理，I/O 與 CPU pipeline 併行化。
2. **穩**：外部工具（LibreOffice/PowerPoint/轉 PDF）必須 timeout + watchdog。
3. **可觀測**：任務能看到目前處理檔案/頁碼/階段，總量/已完成/失敗/跳過/估計剩餘。
4. **可控制**：任務可取消、可暫停、可恢復。
5. **可恢復**：索引過程分段持久化（checkpoint），UI 重啟仍可看到進度。
6. **可部分成功**：單頁失敗不阻斷整體，標記錯誤後繼續。

### 1.2 明確禁止事項

- 禁止等待 UI 回應才繼續。
- 禁止整批完成才落盤；任何 artifact 必須分段落盤。
- 禁止沒有 timeout 的外部子程序。

## 2. 架構總覽

### 2.1 後台獨立進程（daemon）+ 前端沿用

- **Frontend**：透過 API 呼叫後台、訂閱事件流。
- **Backend Daemon**：
  - JobManager（排程/暫停/取消/恢復）
  - Planner（決定頁級任務）
  - Worker Pools（文字/縮圖/向量/BM25）
  - Storage（SQLite/WAL）+ cache 目錄
  - Watchdog（timeout/heartbeat）

## 3. 資料模型（以頁為單位、五旗標）

### 3.1 目錄結構

```
<library_root>/.slidemanager/
  index.sqlite
  thumbs/<file_id>/<page_no>_<aspect>_320x240.jpg
  pdf/<file_id>.pdf
  vectors/
  logs/jobs/<job_id>.log.jsonl
```

### 3.2 核心表格

- `files`：檔案層級
- `pages`：頁級
- `artifacts`：五旗標狀態（text/thumb/text_vec/img_vec/bm25）
- `page_text`：文字抽取結果
- `thumbnails`：縮圖快取
- `embedding_cache_text`：文字向量快取
- `page_text_embedding`、`page_image_embedding`
- `fts_pages`：FTS5 BM25
- `jobs` / `tasks` / `events`

> SQLite DDL 已同步到 `src/app/backend_daemon/schema.sql`。

## 4. 任務系統（Job / Task）

### 4.1 Job 狀態機

`created → planning → running → paused → running → completed`

任何時刻可進入 `cancel_requested → cancelled`。

### 4.2 Task 與 Artifact

- Task 以 `(page_id, kind)` 為單位。
- Artifact 狀態：missing/queued/running/ready/skipped/error/cancelled。

### 4.3 暫停/取消

Worker 在每頁開始與外部 I/O 前檢查 token。

### 4.4 Watchdog

任務 heartbeat 超時必須收斂為 error/cancelled。

## 5. Index Planner

- 初篩：mtime/size
- `text_vec`：比對 `text_sig`（norm_text hash）
- `img_vec`：全重取

## 6. Pipeline

### 6.1 Text Extractor

- 讀 `ppt/slides/slide{page_no}.xml` 抽 `a:t`
- 產出 raw_text/norm_text/text_sig
- 每頁立即落盤

### 6.2 Thumbnail Pipeline

- PPTX → PDF（timeout + kill）
- PDF → Image（逐頁 320×240 或 320×180）

## 7. Embedding

- 空字零向量
- 文字快取（text_sig）
- rate limit + batch + backoff

## 8. BM25

- FTS5 增量更新

## 9. Event Stream

- SSE/WebSocket 事件流
- 每秒快照（counters/ETA/目前任務）

## 10. 失敗處理

- Page 失敗：標記 error，繼續
- File 失敗（PDF）：thumb/img_vec 全部 error，但 text/bm25 可繼續

## 11. API 介面

- `POST /jobs/index`
- `POST /jobs/{job_id}/pause`
- `POST /jobs/{job_id}/resume`
- `POST /jobs/{job_id}/cancel`
- `GET /jobs/{job_id}/events`

## 12. 參考實作

- `src/app/backend_daemon/` 內提供 FastAPI + SSE + JobManager 骨架。
