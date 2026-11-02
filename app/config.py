"""全局配置：读取 .env，暴露路径与 LLM 参数。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@lru_cache(maxsize=1)
def settings() -> dict:
    duckdb_path = ROOT / os.getenv("DUCKDB_PATH", "data/ai_bi.duckdb")
    sqlite_path = ROOT / os.getenv("SQLITE_PATH", "data/chat.db")
    anchor = os.getenv("DATA_ANCHOR_PERIOD", "2025-12")
    year, month = anchor.split("-")
    return {
        "duckdb_path": duckdb_path,
        "sqlite_path": sqlite_path,
        "source_xlsx": os.getenv("SOURCE_XLSX", ""),
        "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        "anchor_year": int(year),
        "anchor_month": int(month),
        "anchor_period": anchor,
        "port": int(os.getenv("APP_PORT", "8000")),
        "root": ROOT,
    }
