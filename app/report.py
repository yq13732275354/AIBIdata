"""一句话生成分析报告：全部数字来自 DuckDB 实算，文字部分由 LLM 基于事实生成。"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app import answer as ans
from app import charts, db, llm
from app.config import settings
from app.semantic import compiler, intent as it

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _run(intent_dict: dict) -> tuple[compiler.Plan, list[list]]:
    intent = it.parse(intent_dict["q"])
    plan = compiler.compile_plan(intent)
    names, rows = db.run_sql(plan.sql, max_rows=500)
    return plan, db.normalize_rows(rows)


def _section(title: str, desc: str, plan, rows) -> dict:
    columns = [{"key": c.key, "label": c.label, "kind": c.kind, "format": c.format} for c in plan.columns]
    return {
        "title": title,
        "desc": desc,
        "columns": columns,
        "rows": rows,
        "chart": charts.build_chart(plan, plan.columns, rows),
        "sql": plan.sql,
    }


def build(title: str = "2025 年度销售分析报告") -> dict:
    cfg = settings()
    year = cfg["anchor_year"]

    # 1) KPI
    _, kpi_rows = _run({"q": f"{year}年总销售额和同比"})
    total = kpi_rows[0][0] if kpi_rows else 0
    yoy = kpi_rows[0][1] if kpi_rows and len(kpi_rows[0]) > 1 else None
    _, qty_rows = _run({"q": f"{year}年总销量和同比"})
    qty = qty_rows[0][0] if qty_rows else 0
    qty_yoy = qty_rows[0][1] if qty_rows and len(qty_rows[0]) > 1 else None

    plat = _run({"q": f"{year}年各平台销售额"})[1]
    country = _run({"q": f"{year}年各国家销售额"})[1]

    kpis = [
        {"label": f"{year} 年销售额", "value": ans.fmt_money(total) + " EUR",
         "delta": ans.fmt_pct(yoy) if yoy is not None else "—", "kind": "up" if (yoy or 0) >= 0 else "down",
         "sub": "同比（vs 上一年）"},
        {"label": f"{year} 年销量", "value": ans.fmt_int(qty) + " 件",
         "delta": ans.fmt_pct(qty_yoy) if qty_yoy is not None else "—", "kind": "up" if (qty_yoy or 0) >= 0 else "down",
         "sub": "同比（vs 上一年）"},
        {"label": "活跃平台", "value": str(len(plat)), "delta": "", "kind": "flat", "sub": "有销售记录的渠道数"},
        {"label": "覆盖国家", "value": str(len(country)), "delta": "", "kind": "flat", "sub": "有销售记录的国家/地区"},
    ]

    sections = [
        _section(
            "月度销售趋势",
            "按月看销售额与同比，识别季节性波动与拐点。",
            *_run({"q": f"{year}年各月的销售额同比"}),
        ),
        _section(
            "TOP10 品类",
            "销售额最高的 10 个三级品类，反映主力产品结构。",
            *_run({"q": f"{year}年Top10品类销售额"}),
        ),
        _section(
            "平台结构",
            "各平台销售额及占比，判断渠道集中度。",
            *_run({"q": f"{year}年各平台销售额占比"}),
        ),
        _section(
            "国家/地区 TOP10",
            "销售额最高的 10 个市场，并给出同比。",
            *_run({"q": f"{year}年Top10国家销售额同比"}),
        ),
    ]

    # 2) 结论：先用确定性摘要，再让 LLM 写解读
    insights = [
        f"销售额结构：TOP3 平台合计占 "
        f"{sum(r[1] for r in plat[:3]) / sum(r[1] for r in plat) * 100:.1f}%，头部集中度较高。"
        if plat else "",
        f"品类集中：TOP10 品类贡献 "
        f"{sum(r[1] for r in sections[1]['rows']) / total * 100:.1f}% 的销售额。" if sections[1]["rows"] else "",
    ]
    insights = [i for i in insights if i]

    if llm.available():
        facts = [
            f"{year} 年销售额 {ans.fmt_money(total)} EUR，同比 {ans.fmt_pct(yoy)}",
            f"{year} 年销量 {ans.fmt_int(qty)} 件，同比 {ans.fmt_pct(qty_yoy)}",
            "TOP5 平台：" + "、".join(f"{r[0]}({ans.fmt_money(r[1])})" for r in plat[:5]),
            "TOP5 品类：" + "、".join(f"{r[0]}({ans.fmt_money(r[1])})" for r in sections[1]["rows"][:5]),
            "TOP5 国家：" + "、".join(f"{r[0]}({ans.fmt_money(r[1])})" for r in sections[3]["rows"][:5]),
        ]
        msgs = [
            {"role": "system", "content": (
                "你是电商销售分析师。只能使用给定事实，严禁编造数字或引入外部信息。"
                "输出 3-4 条中文要点（每条一行，以 - 开头），每条不超过 60 字，"
                "聚焦增长/下滑、结构集中度、值得关注的风险与机会。"
            )},
            {"role": "user", "content": "事实：\n" + "\n".join(facts)},
        ]
        text = llm.chat(msgs, temperature=0.3, max_tokens=600)
        if text:
            for line in text.splitlines():
                line = line.strip().lstrip("-• ").strip()
                if line:
                    insights.append(line)

    now = dt.datetime.now()
    report = {
        "title": title,
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "period": f"{cfg['anchor_period'][:4]} 年（数据范围 {cfg['anchor_period']} 所在年度）",
        "kpis": kpis,
        "insights": insights,
        "sections": sections,
    }

    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=select_autoescape(["html"]))
    env.filters["fmt_cur"] = lambda v: ans.fmt_money(v) if v is not None else "—"
    env.filters["fmt_int"] = lambda v: ans.fmt_int(v) if v is not None else "—"
    env.filters["fmt_pct"] = lambda v: ans.fmt_pct_plain(v) if v is not None else "—"
    tpl = env.get_template("report.html.j2")
    html = tpl.render(**report, sections_json=json.dumps([s["chart"] for s in sections], ensure_ascii=False))

    out_dir = cfg["root"] / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"report_{now.strftime('%Y%m%d_%H%M%S')}.html"
    path = out_dir / fname
    path.write_text(html, encoding="utf-8")

    return {"path": str(path), "url": f"/reports/{fname}", "title": title, "generated_at": report["generated_at"]}
