"""工作台首页的真实数据：KPI、趋势、结构。全部走语义层实算。"""
from __future__ import annotations

from app import charts, db
from app.config import settings
from app.semantic import compiler, intent as it


def _run(q: str) -> tuple[compiler.Plan, list[list]]:
    intent = it.parse(q)
    plan = compiler.compile_plan(intent)
    _, rows = db.run_sql(plan.sql, max_rows=500)
    return plan, db.normalize_rows(rows)


def overview() -> dict:
    year = settings()["anchor_year"]

    total_plan, total_rows = _run("总销售额")
    total_all = total_rows[0][0] if total_rows else 0

    _, yoy_rows = _run(f"{year}年总销售额和同比")
    year_amount = yoy_rows[0][0] if yoy_rows else 0
    year_yoy = yoy_rows[0][1] if yoy_rows and len(yoy_rows[0]) > 1 else None

    _, qty_rows = _run(f"{year}年总销量和同比")
    year_qty = qty_rows[0][0] if qty_rows else 0
    qty_yoy = qty_rows[0][1] if qty_rows and len(qty_rows[0]) > 1 else None

    plat_plan, plat_rows = _run(f"{year}年各平台销售额占比")
    cat_plan, cat_rows = _run(f"{year}年Top10品类销售额")
    country_plan, country_rows = _run(f"{year}年Top10国家销售额同比")
    trend_plan, trend_rows = _run(f"{year}年各月的销售额同比")

    from app.semantic import loader
    catalog = loader.load()["catalog"]

    return {
        "year": year,
        "period_label": f"{year}年",
        "kpi": {
            "total_all": total_all,
            "year_amount": year_amount,
            "year_yoy": year_yoy,
            "year_qty": year_qty,
            "qty_yoy": qty_yoy,
            "platforms": len(catalog["platform"]),
            "categories": len(catalog["category"]),
            "countries": len(catalog["country"]),
        },
        "charts": {
            "trend": {
                "columns": [{"key": c.key, "label": c.label, "kind": c.kind, "format": c.format} for c in trend_plan.columns],
                "rows": trend_rows,
                "chart": charts.build_chart(trend_plan, trend_plan.columns, trend_rows),
            },
            "platform": {
                "columns": [{"key": c.key, "label": c.label, "kind": c.kind, "format": c.format} for c in plat_plan.columns],
                "rows": plat_rows,
                "chart": charts.build_chart(plat_plan, plat_plan.columns, plat_rows),
            },
            "category": {
                "columns": [{"key": c.key, "label": c.label, "kind": c.kind, "format": c.format} for c in cat_plan.columns],
                "rows": cat_rows,
                "chart": charts.build_chart(cat_plan, cat_plan.columns, cat_rows),
            },
            "country": {
                "columns": [{"key": c.key, "label": c.label, "kind": c.kind, "format": c.format} for c in country_plan.columns],
                "rows": country_rows,
                "chart": charts.build_chart(country_plan, country_plan.columns, country_rows),
            },
        },
    }


def dimensions() -> dict:
    from app.semantic import loader
    cat = loader.load()["catalog"]
    return {
        "rows": cat["rows"],
        "period_min": cat["period_min"],
        "period_max": cat["period_max"],
        "total_amount": cat["total_amount"],
        "total_qty": cat["total_qty"],
        "per_year": cat["per_year"],
        "platform": cat["platform"],
        "country": cat["country"],
        "category": cat["category"],
    }
