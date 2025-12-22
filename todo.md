# TODO（個人投影片管理 / Local Slide Manager）

- [x] 建立專案骨架（src/app、logs、data、assets）
- [x] 實作設定與狀態保存（settings.json：上次專案路徑、視窗大小、分頁）
- [x] 實作 ProjectStore（project.json / manifest.json / index.json；版本欄位、原子寫入、.bak）
- [x] 實作 CatalogService（白名單目錄 CRUD、掃描 .pptx、metadata/hash、索引狀態）
- [x] 實作 ExtractionService（python-pptx 逐頁抽取 title/body/all_text）
- [x] 實作 RenderService（LibreOffice 可用則渲染；否則 placeholder 縮圖）
- [x] 實作 SecretsService（本機加密保存 OpenAI API Key）
- [x] 實作 OpenAIClient（Embeddings + Responses 串流）
- [x] 實作 EmbeddingService（OpenAI embeddings；無 Key 則 fallback_hash）
- [x] 實作 ImageEmbedder（ONNX 可用則啟用；否則 fallback_hash）
- [x] 實作 IndexService（增量索引、向量 base64_f32、concat_vec、可取消）
- [x] 實作 SearchService（BM25、向量、Hybrid 權重、文字 tokenization）
- [x] 實作 UI：檔案庫/索引（白名單、掃描、索引、進度、取消）
- [x] 實作 UI：搜尋（文字/Hybrid/向量、以圖搜圖、結果預覽、開啟檔案位置）
- [x] 實作 UI：對話（本機檢索 + 串流回答；無 Key 則僅回結果）
- [x] 實作 UI：設定/診斷（API Key、測試連線、診斷、開啟 logs）
- [x] 單元測試（vectors、project_store）
- [x] 以 project_launcher.py 產生 run_app.bat 與 requirements.txt
- [x] 打包成 ZIP 交付

## Hotfix（2025-12-20）

- [x] 修正 ChatTab f-string（避免 backslash 導致解析失敗）
- [x] 修正 project_launcher.py：pptx -> python-pptx、numpy<2、run_app.bat 以 cp950 寫入
- [x] 修正單元測試：自動加入 src 至 sys.path

## Tech Spec 待辦（依 spec/tech_spec.md）

- [ ] 1) 專案與資料夾規劃
  - [ ] 建立/確認專案目錄結構（project.json、manifest.json、index.json、thumbs/、cache/）
  - [ ] 新增專案版本與遷移策略（schema_version）
- [ ] 2) 目錄白名單與掃描（CatalogService）
  - [ ] 白名單目錄 CRUD（新增/移除/啟用/停用/遞迴）
  - [ ] 掃描 `.pptx` 清單（size/mtime/path）
  - [ ] 讀取 metadata（core properties、slide_count）
  - [ ] missing 檔案標記與 UI 清理流程
- [ ] 3) 索引決策（IndexService）
  - [ ] 判斷未索引/mtime 變更/slide_count 變更需重建
  - [ ] 支援排程、暫停、續跑、取消與進度事件
  - [ ] 索引完成後更新 index_status
- [ ] 4) 文字抽取與縮圖渲染
  - [ ] 文字抽取：title/body/all_text
  - [ ] Renderer 可插拔（LibreOffice headless/Windows COM）
  - [ ] 無 renderer 時允許純文字索引並顯示狀態
- [ ] 5) 向量與索引寫入
  - [ ] 文字 embedding（OpenAI，批次/快取/重試）
  - [ ] 圖片 embedding（ONNX 2048-d，模型快取/版本）
  - [ ] concat 向量與 BM25 tokens 生成
  - [ ] JSON 原子寫入（temp → replace，保留 .bak）
- [ ] 6) 搜尋引擎（BM25 + Vector + Hybrid）
  - [ ] BM25 計分與正規化（min-max）
  - [ ] cosine 正規化與混合分數（權重可調）
  - [ ] 文字/圖片/整體/混合四種模式
- [ ] 7) UI/UX（Tabbed UI）
  - [ ] Library/Index Tab（清單、狀態、索引控制）
  - [ ] Search Tab（文字/圖片/混合搜尋、結果預覽）
  - [ ] Chat Tab（LLM 串流、可取消）
  - [ ] Settings/Diagnostics（白名單、API Key、渲染器狀態）
  - [ ] 長任務背景執行 + 進度條
- [ ] 8) 錯誤處理與日誌
  - [ ] UI 友善錯誤訊息與可重試策略
  - [ ] logs/ 分級日誌（INFO/WARNING/ERROR）
  - [ ] 常見錯誤碼對應（Renderer、OpenAI、JSON、ONNX）
- [ ] 9) Edge/Abuse Cases
  - [ ] 權限不足與路徑移除處理
  - [ ] 文字為空、縮圖失敗的降級行為
  - [ ] mtime 變動後二次檢查
- [ ] 10) 測試與驗收
  - [ ] 功能測試（掃描、索引、搜尋、不中斷 UI）
  - [ ] Gherkin 範例覆蓋（僅變更檔案重建）
  - [ ] 驗收條件對照清單
