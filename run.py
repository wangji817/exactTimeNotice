#!/usr/bin/env python
"""三国群雄传平台司令台 — 启动入口。"""

from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8765,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
