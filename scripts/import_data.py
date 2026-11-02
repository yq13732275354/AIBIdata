"""把 saledata-test.xlsx 导入 DuckDB，并产出维度值域字典供语义层做值域召回。

用法：
    python3 scripts/import_data.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings  # noqa: E402

COLUMNS = [
    "order_year",
    "order_month",
    "platform_name",
    "country_name",
    "cate_lvl3_cn",
    "trd_amt_eur",
    "trd_prod_qty",
]


def main() -> None:
    cfg = settings()
    src = Path(cfg["source_xlsx"])
    if not src.exists():
        raise SystemExit(f"源文件不存在: {src}")

    db_path = cfg["duckdb_path"]
    db_path.parent.mkdir(parents=True, exist_ok=True)

    df = pd.read_excel(src, sheet_name="Sheet1", dtype={"order_year": "int64", "order_month": "int64"})
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise SystemExit(f"缺少列: {missing}")

    df = df[COLUMNS].copy()
    for col in ("platform_name", "country_name", "cate_lvl3_cn"):
        df[col] = df[col].astype("string").str.strip()
        df[col] = df[col].where(df[col].str.len() > 0, None)
    df["order_year"] = df["order_year"].astype("int64")
    df["order_month"] = df["order_month"].astype("int64")
    df["trd_amt_eur"] = df["trd_amt_eur"].astype("float64")
    df["trd_prod_qty"] = df["trd_prod_qty"].fillna(0).astype("int64")

    # 语义层需要的时间列：period='YYYY-MM'，month_start=当月 1 号（便于跨期比较）
    period = df["order_year"].astype(str).str.zfill(4) + "-" + df["order_month"].astype(str).str.zfill(2)
    df["period"] = period.astype("string")
    df["month_start"] = pd.to_datetime(period + "-01", format="%Y-%m-%d")

    con = duckdb.connect(str(db_path))
    con.execute("DROP TABLE IF EXISTS sales")
    con.execute(
        """
        CREATE TABLE sales (
            order_year   INTEGER,
            order_month  INTEGER,
            platform_name VARCHAR,
            country_name  VARCHAR,
            cate_lvl3_cn  VARCHAR,
            trd_amt_eur   DOUBLE,
            trd_prod_qty  BIGINT,
            period        VARCHAR,
            month_start   DATE
        )
        """
    )
    con.register("src_df", df)
    con.execute("INSERT INTO sales SELECT * FROM src_df")
    con.unregister("src_df")

    con.execute("CREATE INDEX IF NOT EXISTS idx_sales_period ON sales(period)")

    # 值域字典：用于同义词 + 候选召回，避免把 144 个品类全塞进 prompt
    def uniq(col: str) -> list[str]:
        rows = con.execute(
            f"SELECT {col} AS v FROM sales WHERE {col} IS NOT NULL GROUP BY 1 ORDER BY 1"
        ).fetchall()
        return [r[0] for r in rows]

    years = con.execute("SELECT DISTINCT order_year FROM sales ORDER BY 1").fetchall()
    per_year = con.execute(
        "SELECT order_year, COUNT(*) AS rows, SUM(trd_amt_eur) AS amt FROM sales GROUP BY 1 ORDER BY 1"
    ).fetchall()
    total = con.execute("SELECT COUNT(*), SUM(trd_amt_eur), SUM(trd_prod_qty), MIN(period), MAX(period) FROM sales").fetchone()

    catalog = {
        "platform": uniq("platform_name"),
        "country": uniq("country_name"),
        "category": uniq("cate_lvl3_cn"),
        "years": [r[0] for r in years],
        "period_min": total[3],
        "period_max": total[4],
        "rows": total[0],
        "total_amount": float(total[1] or 0),
        "total_qty": int(total[2] or 0),
        "per_year": {str(r[0]): {"rows": r[1], "amount": float(r[2] or 0)} for r in per_year},
    }

    out = Path(__file__).resolve().parent.parent / "app" / "semantic" / "values.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"导入完成: {db_path}")
    print(f"行数={catalog['rows']} 期间={catalog['period_min']}~{catalog['period_max']}")
    print(f"销售额合计={catalog['total_amount']:,.2f} EUR  销量={catalog['total_qty']:,}")
    for y, info in catalog["per_year"].items():
        print(f"  {y}: {info['rows']} 行, {info['amount']:,.2f} EUR")
    print(f"值域: 平台{len(catalog['platform'])} 国家{len(catalog['country'])} 品类{len(catalog['category'])}")
    print(f"值域字典 -> {out}")
    con.close()


if __name__ == "__main__":
    main()
