# TODO（個人投影片管理 / Local Slide Manager）

- [x] 建立專案骨架（src/app、logs、data、assets）
- [x] 實作設定與狀態保存（settings.json：上次專案路徑、視窗大小、分頁）
- [x] 實作 ProjectStore（project.json / catalog.json / index.json；版本欄位、原子寫入、.bak）
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

