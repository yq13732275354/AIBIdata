"""问数主流程：解析 → 编译 → 执行 → 出图 → 结论。语义层优先，NL2SQL 兜底。"""
from __future__ import annotations

import logging
import time

from app import answer as ans
from app import charts, db, llm, nl2sql
from app.semantic import compiler, guard, intent as it

log = logging.getLogger("aibi.agent")

# 语义层未覆盖的聚合口径，直接走 NL2SQL
ADVANCED_WORDS = ["平均", "中位数", "单笔", "客单价", "复合", "cagr", "方差", "标准差", "占比排名"]


def _base(type_: str, question: str) -> dict:
    return {
        "type": type_,
        "question": question,
        "headline": "",
        "bullets": [],
        "columns": [],
        "rows": [],
        "chart": None,
        "sql": "",
        "period": "",
        "notes": [],
        "insight": None,
        "source": "",
        "slots": {},
    }


def _columns_from_names(names: list[str], rows: list[list]) -> list[dict]:
    cols = []
    for i, n in enumerate(names):
        numeric = all(isinstance(r[i], (int, float)) and r[i] is not None for r in rows) if rows else False
        cols.append({"key": n, "label": n, "kind": "metric" if numeric else "dim",
                     "format": "number" if numeric else "text"})
    return cols


def ask(question: str, slots: dict | None = None, want_insight: bool = True) -> dict:
    t0 = time.perf_counter()
    question = (question or "").strip()
    if not question:
        p = _base("error", question)
        p["headline"] = "问题为空。"
        return p

    # ---------- 语义层 ----------
    need_nl2sql = any(w in question.lower() for w in ADVANCED_WORDS)
    if not need_nl2sql:
        try:
            intent = it.parse(question, slots)
            plan = compiler.compile_plan(intent)
            names, rows = db.run_sql(plan.sql)
            rows = db.normalize_rows(rows)
            columns = [
                {"key": c.key, "label": c.label, "kind": c.kind, "format": c.format}
                for c in plan.columns
            ]
            payload = _base("answer" if rows else "nodata", question)
            payload.update({
                "columns": columns,
                "rows": rows,
                "chart": charts.build_chart(plan, plan.columns, rows),
                "sql": plan.sql,
                "period": plan.period_label,
                "notes": plan.notes,
                "source": "semantic",
                "slots": intent.slots(),
            })
            summary = ans.summarize(plan, plan.columns, rows) if rows else {"headline": "该口径下没有数据。", "bullets": []}
            payload["headline"] = summary.get("headline", "")
            payload["bullets"] = summary.get("bullets", [])
            if not rows:
                payload["headline"] = f"{plan.period_label} 在 {_filter_text(intent)} 下没有数据。"
            payload["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
            if want_insight and rows:
                payload["insight"] = _insight(question, payload)
            return payload
        except it.Clarify as e:
            p = _base("clarify", question)
            p["headline"] = e.question
            p["notes"] = e.options
            p["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
            return p
        except compiler.NoData as e:
            p = _base("nodata", question)
            p["headline"] = e.message
            p["notes"] = [e.suggestion] if e.suggestion else []
            p["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
            return p
        except guard.SQLRejected as e:
            log.warning("语义层 SQL 被拒: %s", e)
            need_nl2sql = True
        except Exception as e:  # noqa: BLE001
            log.exception("语义层执行失败，转 NL2SQL: %s", e)
            need_nl2sql = True

    # ---------- NL2SQL 兜底 ----------
    ctx = ""
    if slots:
        ctx = f"上一轮已确认的查询条件：{slots}"
    res = nl2sql.generate(question, ctx)
    if not res.get("ok"):
        p = _base("error", question)
        p["headline"] = "这个问题暂时无法生成可靠的查询。"
        p["notes"] = [res.get("error", ""), "可以换个说法，或参考示例：2025 年各平台销售额同比 / Top10 品类销售额。"]
        p["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
        return p

    payload = _base("answer" if res["rows"] else "nodata", question)
    columns = _columns_from_names(res["names"], res["rows"])
    payload.update({
        "columns": columns,
        "rows": res["rows"],
        "chart": charts.build_generic_chart(res["names"], res["rows"], res.get("chart_type", "table")),
        "sql": res["sql"],
        "period": "NL2SQL",
        "notes": [res.get("explanation", "")] if res.get("explanation") else [],
        "source": "nl2sql",
    })
    main = next((c for c in columns if c["kind"] == "metric"), None)
    if res["rows"] and main:
        i = columns.index(main)
        first = res["rows"][0]
        payload["headline"] = f"共返回 {len(res['rows'])} 行结果，首行 {main['label']} = {first[i]:,.2f}。" if isinstance(first[i], (int, float)) else "查询完成。"
    else:
        payload["headline"] = "该口径下没有数据。"
    payload["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
    if want_insight and res["rows"]:
        payload["insight"] = _insight(question, payload)
    return payload


def _filter_text(intent) -> str:
    if not intent.filters:
        return "全部范围"
    return "、".join(f"{k}={','.join(v)}" for k, v in intent.filters.items())


def _insight(question: str, payload: dict) -> str | None:
    """让 LLM 基于已算好的事实写 2-3 句解读；失败返回 None，前端只用确定性结论。"""
    if not llm.available():
        return None
    cols = payload["columns"]
    facts = [f"口径：{payload['period']}", f"结论：{payload['headline']}"]
    facts += [f"要点：{b}" for b in payload["bullets"][:4]]
    head = [r for r in payload["rows"][:6]]
    table = " | ".join(c["label"] for c in cols) + "\n" + "\n".join(
        " | ".join(str(v) for v in r) for r in head
    )
    messages = [
        {
            "role": "system",
            "content": (
                "你是电商销售数据分析师。只能使用用户提供的查询事实，严禁编造数字、"
                "不要引入数据集以外的信息、不要给投资建议。用 2-3 句中文说清业务含义与关注点，"
                "总长不超过 100 字，不要复述表格。"
            ),
        },
        {"role": "user", "content": f"用户问题：{question}\n\n查询事实：\n" + "\n".join(facts) + f"\n\n结果明细（前 6 行）：\n{table}"},
    ]
    text = llm.chat(messages, temperature=0.2, max_tokens=300)
    return (text or "").strip() or None
