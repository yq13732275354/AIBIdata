"""DuckDB 访问层：线程局部连接 + 结果规范化。"""
from __future__ import annotations

import datetime as dt
import decimal
import threading

import duckdb

from app.config import settings

_local = threading.local()


def get_conn() -> duckdb.DuckDBPyConnection:
    con = getattr(_local, "con", None)
    if con is None:
        con = duckdb.connect(str(settings()["duckdb_path"]), read_only=True)
        con.execute("SET threads=4")
        _local.con = con
    return con


def run_sql(sql: str, max_rows: int = 2000) -> tuple[list[str], list[list]]:
    con = get_conn()
    cur = con.execute(sql)
    names = [d[0] for d in cur.description] if cur.description else []
    rows = cur.fetchmany(max_rows)
    return names, [list(r) for r in rows]


def jsonable(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()[:10]
    if isinstance(v, dt.time):
        return v.isoformat()
    return v


def normalize_rows(rows: list[list]) -> list[list]:
    return [[jsonable(v) for v in r] for r in rows]
