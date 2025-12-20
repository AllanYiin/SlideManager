# -*- coding: utf-8 -*-

"""Local Slide Manager - 程式入口（python -m app）。

硬規則：
- 全域 try/except，避免閃退。
- 錯誤訊息需可複製，並寫入 logs。
"""

from __future__ import annotations

import traceback

from app.core.logging import get_logger, setup_logging


def main() -> int:
    setup_logging()
    log = get_logger(__name__)

    try:
        from app.main import run

        return run()
    except Exception:
        tb = traceback.format_exc()
        log.error("未捕捉例外（程式將結束）\n%s", tb)

        # 避免使用者只看到閃退：在 console 印出可複製訊息
        print("\n發生錯誤。請複製以下訊息並發送給您的 AI 助手：\n")
        print(tb)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
