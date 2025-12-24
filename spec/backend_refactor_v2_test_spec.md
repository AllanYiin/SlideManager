# 後台重構（Job/Task/Artifact 五旗標 + SSE + 可暫停/取消 + 逐頁 checkpoint + timeout/watchdog）單元測試規格

## 0) 測試分層與基本要求

### 0.1 測試分層（建議）

1. **Pure Unit Tests（純單元）**

   * 不啟動 FastAPI server
   * 不真的跑 LibreOffice / PowerPoint
   * 不打 OpenAI
   * 只測：函式/類別輸入輸出、DB 變更、狀態機、rate limiter、事件匯流排
2. **Fast Integration Tests（快速整合）**

   * 用 SQLite（temp file）
   * 用 FastAPI TestClient（或 httpx AsyncClient）
   * 但外部依賴一律 mock（subprocess / OpenAI / PyMuPDF）
3. **Optional System Tests（可選、標記 slow / windows-only）**

   * 有安裝 LibreOffice 時才跑
   * 測 PPTX→PDF→縮圖一條龍
   * 放到 nightly 或手動 workflow（避免 CI 不穩）

> CI 預設只跑 Unit + Fast Integration；System tests 用 marker 控制。

### 0.2 品質 Gate（建議）

* **核心模組（job_manager / planner / rate_limit / event_bus / text_extract）覆蓋率 >= 85%**
* **任何 PR 不允許新增 “永遠等待” 的 code path**
  → 必須有對應 watchdog/timeout 測試案例。

---

## 1) 測試資料（Fixtures）規格

### 1.1 PPTX fixture 生成器（不依賴 python-pptx）

以 zip 直接寫 OpenXML 檔案：

* `ppt/presentation.xml`：包含 `<p:sldSz cx cy>` 用來判斷 4:3 / 16:9
* `ppt/slides/slide1.xml`、`slide2.xml`…：內含 `<a:t>` 節點文字

必備的 PPTX fixture 類型：

1. **PPTX_4_3_3pages**：3 頁，4:3，文字正常
2. **PPTX_16_9_3pages**：3 頁，16:9，文字正常
3. **PPTX_mixed_text**：含空白頁（無 `<a:t>`）、含只有空白/換行的頁
4. **PPTX_repeated_footer**：每頁含同一句高頻頁腳（測句頻分析/去冗策略）
5. **PPTX_corrupt_slide_xml**：某一頁 XML 故意寫壞（測 “失敗跳過下一頁”）

### 1.2 PDF fixture

* **Fake PDF**：不一定要真 PPTX 轉出；可用 PyMuPDF 直接產生一個 3 頁 PDF（每頁寫矩形/文字即可）
* 測縮圖時用 mock render 也可以，但至少要有一份真 PDF 來測 `render_pdf_page_to_thumb()` 的尺寸與輸出檔存在

### 1.3 DB fixture

* 每個測試用獨立 temp 資料夾
* `.slidemanager/index.sqlite` 寫在 temp
* schema.sql 每次 init
* WAL 開啟驗證（可在測試中檢查 pragma）

---

## 2) 模組級單元測試規格

### 2.1 `text_extract.py`（最核心：逐頁文字抽取 + 正規化 + text_sig）

#### Case TE-01：抽取 `<a:t>` 組合成文字

* Given：一個 slide xml 含多個 `<a:t>`（跨多個段落）
* When：呼叫 `extract_text_from_slide_xml(xml_bytes)`
* Then：

  * 回傳文字包含所有 `<a:t>` 的順序串接
  * 使用 `\n` 分隔（符合你後續句頻分析的需求）

#### Case TE-02：normalize_text 移除零寬字元與多餘空白

* Given：字串含 `\u200b`、多個空白、`CRLF`
* When：`normalize_text()`
* Then：

  * 零寬字元被移除
  * `CRLF` → `\n`
  * 行內多空白壓縮成單一空白
  * 空行被去除
  * 行順序保留

#### Case TE-03：空文字回傳空 sig

* Given：raw 抽出來為空或 normalize 後為空
* When：`extract_page_text()` 或 `fast_text_sig()`
* Then：`sig == ""`（後續 zero vector 判斷依此）

#### Case TE-04：text_sig 穩定性

* Given：同樣 norm_text 呼叫兩次
* Then：sig 相同
* Given：norm_text 改一個字
* Then：sig 不同

---

### 2.2 `pptx_meta.py`（判斷 4:3 / 16:9）

#### Case PM-01：4:3 判斷

* Given：presentation.xml `cx/cy` 比例接近 4/3
* When：`detect_aspect_from_pptx()`
* Then：回傳 `"4:3"`

#### Case PM-02：16:9 判斷

* Given：比例接近 16/9
* Then：回傳 `"16:9"`

#### Case PM-03：缺 sldSz 或不合法值

* Given：沒有 `<p:sldSz>` 或 cx/cy=0
* Then：回傳 `"unknown"`

#### Case PM-04：PPTX 結構不完整（zip 缺檔）

* Given：pptx 缺 `ppt/presentation.xml`
* Then：回傳 `"unknown"`（不可 throw 讓 job 掛掉）

---

### 2.3 `planner.py`（mtime/size 初篩 + pages/artifacts 建置）

#### Case PL-01：scan_files_under 只掃當層、不遞迴

* Given：root 下有 pptx；子資料夾下有 pptx
* When：`scan_files_under(root)`
* Then：只回傳 root 當層 pptx

#### Case PL-02：upsert_file 新增檔案

* Given：DB 無此 path
* When：`upsert_file()`
* Then：

  * files 增加 1 筆
  * `path,size,mtime,slide_aspect` 正確

#### Case PL-03：upsert_file 更新 mtime/size

* Given：files 已有該 path
* When：用不同 mtime/size 呼叫 upsert
* Then：該 row 被更新，file_id 不變

#### Case PL-04：ensure_pages_rows 建立 pages 與五旗標 artifacts

* Given：檔案 slide_count=3
* When：`ensure_pages_rows()`
* Then：

  * pages 有 3 筆
  * 每個 page 有 5 筆 artifacts(kind=text/thumb/text_vec/img_vec/bm25)
  * 其 status 預設為 `missing`
  * UNIQUE(file_id,page_no) 正常（重跑不會重複）

#### Case PL-05：file_changed 判定

* 測 size 改、mtime 改、都不改三種情況

---

### 2.4 `db.py`（WAL/pragma/連線）

#### Case DB-01：open_db 啟用 WAL 與 foreign keys

* When：open_db
* Then：

  * `PRAGMA journal_mode` 為 WAL
  * `PRAGMA foreign_keys=ON`

#### Case DB-02：busy_timeout 生效

* 可用 PRAGMA 查回來驗證

---

### 2.5 `bm25.py`（FTS5 增量 upsert）

#### Case BM-01：upsert 新頁

* Given：fts_pages 空
* When：`upsert_fts_page(page_id, "hello world")`
* Then：fts_pages 有該 row

#### Case BM-02：upsert 覆寫舊文字

* Given：先寫 "hello"
* When：再寫 "bye"
* Then：fts_pages norm_text 變成 "bye"

#### Case BM-03：空文字也可 upsert

* Then：fts_pages norm_text 為 ""（或至少存在 row）

---

### 2.6 `event_bus.py`（避免 UI 壓垮後台、事件格式）

#### Case EB-01：每個 job 獨立 seq 遞增

* Given：publish 3 次
* Then：seq=1,2,3

#### Case EB-02：queue 滿時丟最舊（不阻塞）

* Given：queue maxsize=例如 3（測試可調小）
* When：publish 10 次
* Then：

  * publish 不會卡住
  * subscribe 取到的是較新的事件（序號較大者存在）

#### Case EB-03：sse_format 輸出符合 SSE 協定

* Then：

  * 以 `data: ` 開頭
  * 以 `\n\n` 結尾
  * JSON 可 parse

---

### 2.7 `rate_limit.py`（雙桶限流 + backoff）

#### Case RL-01：acquire 在 token 足夠時立即返回

* Given：req_per_min/tok_per_min 大、cost 小
* When：acquire
* Then：不 sleep 太久（測試用 fake clock 或把 sleep patch 成記錄呼叫次數）

#### Case RL-02：token 不足會等待且不會 busy loop

* Given：req_per_min 非常低
* When：連續 acquire
* Then：會呼叫 sleep（次數有限），不會 spin

#### Case RL-03：backoff_delay 指數成長且帶 jitter

* When：attempt=0..5
* Then：

  * 大致遞增
  * 不超 cap
  * 同 attempt 兩次結果不完全相同（jitter）

> 建議：在單元測試中把 random 固定 seed，避免 flaky。

---

### 2.8 `embedder.py`（OpenAI embedding：空字零向量、cache、retry/backoff、batch）

這裡重點是「**不打網路**」，用 mock OpenAI client。

#### Case EM-01：estimate_tokens 大致合理且 >=1

* 任何輸入長度回傳 >=1

#### Case EM-02：zero_vector 長度正確

* Given：dim=3072
* When：`zero_vector(3072)`
* Then：bytes 長度 == 3072 * 4（float32）

#### Case EM-03：embed_text_batch_openai 正常回傳 embeddings

* Given：mock client.embeddings.create 回傳固定向量
* When：呼叫 embed_text_batch_openai
* Then：回傳 list 長度=inputs 長度、每個 embedding dim 正確

#### Case EM-04：遇到 429/5xx 會重試，超過 max_retries 才 throw

* Given：mock create 前 N 次丟例外，後一次成功
* Then：成功回傳，create 被呼叫 N+1 次
* Given：一直失敗
* Then：丟出例外，呼叫次數 = max_retries+1

#### Case EM-05：rate limiter acquire 會被呼叫

* Given：patch limiter.acquire 記錄參數
* Then：req_cost=1，tok_cost>0（依 texts 估算）

---

### 2.9 `pdf_convert.py`（timeout + kill 子程序樹 + profile isolation）

不跑真的 LibreOffice，完全用 subprocess mock。

#### Case PC-01：timeout 會 kill 並 raise

* Given：mock Popen.communicate 永遠不回 / 直接丟 TimeoutExpired
* When：convert_pptx_to_pdf_libreoffice(timeout_sec=1)
* Then：

  * 會呼叫 kill_process_tree_windows(proc.pid)（Windows 路徑）
  * raise RuntimeError 包含 timeout 訊息

#### Case PC-02：returncode 非 0 會 raise

* Given：communicate 回來 rc != 0
* Then：raise RuntimeError

#### Case PC-03：expected PDF 不存在會 raise

* Given：模擬成功但 outdir 沒有產物
* Then：raise

#### Case PC-04：有產物則會 rename 成固定 out_pdf

* Given：outdir 產生 `<stem>.pdf`
* Then：最後 out_pdf 存在，且 `<stem>.pdf` 被搬移

---

### 2.10 `thumb_render.py`（PDF→縮圖、尺寸必對）

#### Case TR-01：thumb_size 4:3 回 320x240

* Then：正確

#### Case TR-02：thumb_size 16:9 回 320x180

* Then：正確

#### Case TR-03：unknown 預設策略一致（你要 16:9 或 4:3 都可以，但要測固定）

* Then：輸出固定且不漂移

#### Case TR-04：render_pdf_page_to_thumb 輸出檔存在且尺寸接近指定

* Given：一個 1~2 頁 PDF fixture
* When：render
* Then：

  * out_path 存在
  * 用 PIL 或 PyMuPDF 讀回寬高 == 指定（或容許 1px 誤差）

---

## 3) JobManager（最關鍵：無窮等待免疫、逐頁 checkpoint、失敗跳過、pause/cancel）

### 3.1 規劃階段（planning）測試

#### Case JM-P-01：create_job 會寫 jobs row 並發 job_created 事件

* Given：temp DB + EventBus
* When：create_job
* Then：

  * jobs 有一筆 status=created/planning（依時序）
  * EventBus 送出 `job_created`

#### Case JM-P-02：planning 會建立 pages + artifacts 五旗標

* Given：library_root 有 PPTX_3pages
* When：run job 到 planning 完成
* Then：

  * pages=3
  * artifacts 每頁=5
  * 依 options 決定哪些被 queued（例如 enable_text=true 時 text=queued）

---

### 3.2 逐頁落盤（checkpoint）測試

#### Case JM-C-01：每處理 1 頁就能在 DB 看見 page_text 與 artifacts(text)=ready

* Given：commit_every_pages=1
* When：跑 text worker
* Then：

  * 第一頁做完後立刻查 DB：page_text 有 row、artifacts(text)=ready
  * 第二頁還沒做完前 DB 仍保持第一頁結果

#### Case JM-C-02：commit_every_sec 生效（時間型 checkpoint）

* Given：commit_every_pages 設大、commit_every_sec 設很小
* When：跑 text worker
* Then：即便頁數未達也會 commit（可用 patch time.monotonic 控制）

---

### 3.3 失敗跳過測試（你硬性要求）

#### Case JM-S-01：某頁 slide XML 壞掉 → 該頁 artifacts(text)=error，但下一頁仍會成功

* Given：PPTX_corrupt_slide_xml：page2 xml 損壞
* When：跑 text worker
* Then：

  * page1: text=ready
  * page2: text=error，error_code=TEXT_EXTRACT_FAIL
  * page3: text=ready
  * job 不因 page2 失敗而 failed

---

### 3.4 Pause / Resume 測試（你硬性要求）

#### Case JM-PR-01：pause 後不再有新頁完成

* Given：一個 50 頁 pptx（fixture 生成即可）
* When：

  * start job
  * 等待已完成數 > 0
  * 呼叫 pause_job
  * sleep 一小段（或用事件同步）
* Then：

  * 完成數在 pause 後維持不變（或只允許當前 running 的那一頁完成，但不可繼續下一頁）

#### Case JM-PR-02：resume 後繼續往下跑

* When：resume_job
* Then：完成數持續增加，最後完成

> 測試不要用很長 sleep，建議用 event_bus 收到 `artifact_state_changed` 來同步。

---

### 3.5 Cancel 測試（你硬性要求）

#### Case JM-CA-01：cancel 後所有 queued/running tasks 收斂成 cancelled，job=cancelled

* Given：job 正在跑
* When：cancel_job
* Then：

  * jobs.status=cancelled
  * tasks.status：queued/running 變 cancelled
  * artifacts.status：queued/running 變 cancelled
  * 不會再有新的 artifact_state_changed（或最多只允許 in-flight 的最後一次）

---

### 3.6 Watchdog（徹底消滅無窮等待）測試

#### Case JM-WD-01：running task heartbeat 過期 → watchdog 會標 error 並送 task_error event

* Given：

  * tasks 表手動插入一筆 status=running，heartbeat_at = now-999
* When：跑 watchdog loop 一輪（建議把 watchdog loop 拆成 `_watchdog_tick()` 方便測）
* Then：

  * task.status=error
  * error_code=WATCHDOG_TIMEOUT
  * EventBus 出現 `task_error`

---

## 4) API / SSE（契約測試：前端接得起來）

### 4.1 `POST /jobs/index` 契約

#### Case API-01：回傳 job_id（格式不重要，但必存在）

* When：POST /jobs/index
* Then：JSON 有 `job_id` 字串

#### Case API-02：錯誤輸入回 4xx 且 message 清楚

* Given：library_root 不存在
* Then：回 400/422 + 明確 payload（避免 UI 無限等）

---

### 4.2 `POST /pause|resume|cancel` 契約

#### Case API-03：pause/resume/cancel 回 {ok:true}

* Then：HTTP 200 + ok true
* 取消後再取消：也應回 ok true（idempotent）

---

### 4.3 `GET /jobs/{job_id}/events`（SSE）契約

#### Case SSE-01：第一筆一定送 hello（或固定型事件）

* Then：SSE 第一個 data 可 parse，包含 job_id/type

#### Case SSE-02：stats_snapshot payload 欄位完整

* Given：job 在跑
* Then：至少包含：

  * payload.counters（五旗標）
  * payload.now_running（可為 null 但欄位要存在）
  * payload.rates（可選但建議存在）
* 目的：前端不用做大量 if/else 防呆

---

### 4.4 `GET /jobs/{job_id}`（強烈建議補）契約

#### Case API-04：SSE 掛掉時仍可查詢進度（避免 UI 以為卡住）

* Then：回傳：

  * job.status
  * counters 五旗標
  * now_running（或 last_running）
  * errors 摘要（error count）

---

## 5) 專門針對「OpenAI rate limit + 去重」的測試規格

### 5.1 空文字零向量（不發查）

#### Case OA-01：norm_text 為空 → 不呼叫 embeddings.create

* Given：page_text.norm_text=""
* When：跑 text_vec worker
* Then：

  * embeddings.create 呼叫次數=0
  * artifacts(text_vec)=ready
  * page_text_embedding 有 row（指向 **zero** 或固定 key）
  * vector_blob 長度正確

---

### 5.2 重複文字 cache（不重查）

#### Case OA-02：兩頁 text_sig 相同 → embeddings.create 只呼叫一次

* Given：page1/page2 norm_text 完全相同 → sig 相同
* When：跑 text_vec worker
* Then：

  * OpenAI 被呼叫一次
  * embedding_cache_text 只有一筆（model+sig）
  * page_text_embedding 兩筆都指向同一 sig
  * artifacts(text_vec) 兩頁都 ready

---

### 5.3 遇到 429/5xx：backoff + retry

#### Case OA-03：前 2 次丟 429，第三次成功

* Then：

  * 呼叫次數=3
  * 最終 ready
  * 不會把整個 job 卡死（仍可繼續後面的 batch）

---

## 6) 針對縮圖與 PDF 轉換（最容易卡住）必寫測試

### 6.1 PDF timeout 一定收斂（不會 running 永久）

#### Case PDF-01：subprocess timeout → file task error → 該檔所有 thumb artifacts error

* Given：mock LO timeout
* When：跑 pdf task
* Then：

  * 해당 file 的 pdf task status=error
  * pages 的 artifacts(thumb)=error（每頁都有）
  * job 仍繼續下一檔（若有）

---

### 6.2 縮圖輸出尺寸固定

#### Case TH-01：4:3 → 320x240

* Then：輸出檔存在且尺寸正確

#### Case TH-02：16:9 → 320x180

* Then：同上

---

## 7) 測試目錄與標記（pytest）

### 7.1 建議目錄

```
tests/
  unit/
    test_text_extract.py
    test_pptx_meta.py
    test_planner.py
    test_rate_limit.py
    test_event_bus.py
    test_bm25.py
    test_pdf_convert.py
    test_thumb_render.py
  integration/
    test_job_manager_text_bm25.py
    test_job_manager_pause_cancel_watchdog.py
    test_api_contract.py
    test_sse_contract.py
  system/   (optional)
    test_libreoffice_pipeline_windows.py
```

### 7.2 pytest markers（建議）

* `@pytest.mark.unit`
* `@pytest.mark.integration`
* `@pytest.mark.system`
* `@pytest.mark.windows_only`
* `@pytest.mark.slow`

CI 預設：

* `pytest -m "unit or integration"`
  nightly：
* `pytest -m system`

---

## 8) 明確「不可接受」的失敗（測試要抓出來）

1. **任何 running 任務沒有 heartbeat 更新**

   * 必須被 watchdog 收斂（測 JM-WD-01）
2. **任何外部轉檔 subprocess 沒有 timeout**

   * 必須有 timeout 測試（PC-01 / PDF-01）
3. **任何 job 在 pause/cancel 後仍繼續吞頁**

   * 必須被 pause/cancel 測試擋住（JM-PR / JM-CA）
4. **索引不落盤直到最後**

   * 必須有逐頁 checkpoint 測試（JM-C-01 / JM-C-02）
5. **單頁失敗導致整個 job 掛掉**

   * 必須有失敗跳過測試（JM-S-01）
