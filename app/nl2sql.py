"""NL2SQL 兜底：只在语义层覆盖不到时启用，产出必须通过 AST 白名单校验。"""
from __future__ import annotations

from app import db, llm
from app.semantic import guard, loader

SYSTEM_PROMPT = """你是电商销售数据集的 SQL 生成器，目标数据库是 DuckDB。

{schema}

硬性规则（违反将直接失败）：
1. 只输出一条 SELECT 查询，禁止 INSERT/UPDATE/DELETE/CREATE/DROP 等任何写操作；
2. 只允许查询表 sales，禁止 JOIN、禁止查询其他表；
3. 只能使用上面列出的列名，不要臆造字段；
4. 时间列 period 是 'YYYY-MM' 字符串，跨期比较用 period BETWEEN '2025-01' AND '2025-12'；
5. 国家、平台的值是英文，品类是中文，必须使用数据中真实存在的值；
6. 金额单位是欧元（EUR），销量是件；
7. 结果最多返回 200 行，明细需要聚合后再排序。

只输出 JSON，格式：
{{"sql": "...", "chart_type": "line|bar|pie|donut|area|table", "explanation": "一句话说明查询口径"}}"""


def _schema_text() -> str:
    sem = loader.load()["semantic"]
    cat = loader.load()["catalog"]
    lines = [f"表 sales（粒度：年×月×平台×国家×品类，共 {cat['rows']:,} 行，期间 {cat['period_min']}~{cat['period_max']}）："]
    for name, cfg in sem["columns"].items():
        lines.append(f"  - {name} {cfg['type']}  # {cfg['label']}")
    lines.append("")
    lines.append("指标口径：销售额 = SUM(trd_amt_eur)；销量 = SUM(trd_prod_qty)。")
    lines.append("值域：")
    lines.append("  platform_name ∈ " + ", ".join(cat["platform"]))
    lines.append("  country_name ∈ " + ", ".join(cat["country"]))
    lines.append("  cate_lvl3_cn：143 个中文品类，示例 " + "、".join(cat["category"][:12]) + " 等")
    return "\n".join(lines)


def _candidate_text(question: str) -> str:
    """把问题里可能涉及的品类候选召回出来，避免全量注入 143 个品类。"""
    cats = loader.recall_values("category", question, top_k=12)
    if not cats:
        return ""
    return "问题可能涉及的品类候选（必须用其中之一）：" + "、".join(cats)


def generate(question: str, context: str = "") -> dict:
    """返回 {ok, sql, chart_type, explanation, error}。失败不抛异常。"""
    if not llm.available():
        return {"ok": False, "error": "未配置 DeepSeek Key，无法使用 NL2SQL 兜底。"}

    schema = _schema_text()
    system = SYSTEM_PROMPT.format(schema=schema)
    user_parts = [f"问题：{question}"]
    cand = _candidate_text(question)
    if cand:
        user_parts.append(cand)
    if context:
        user_parts.append(context)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(user_parts)},
    ]

    last_error = ""
    for attempt in range(3):  # 首次 + 最多 2 次重试
        data = llm.chat_json(messages)
        if not data or not data.get("sql"):
            last_error = "模型未返回可用的 SQL"
            messages.append({"role": "user", "content": "请严格按 JSON 格式返回 sql 字段。"})
            continue
        raw_sql = str(data["sql"])
        try:
            sql = guard.validate(raw_sql)
        except guard.SQLRejected as e:
            last_error = str(e)
            messages.append({"role": "user", "content": f"上一次 SQL 未通过安全校验：{e}。请修正后重新输出 JSON。"})
            continue

        try:
            names, rows = db.run_sql(sql, max_rows=500)
        except Exception as e:  # noqa: BLE001
            last_error = f"SQL 执行失败：{e}"
            messages.append({"role": "user", "content": f"上一次 SQL 执行报错：{e}。请修正后重新输出 JSON。"})
            continue

        return {
            "ok": True,
            "sql": sql,
            "chart_type": str(data.get("chart_type") or "table").lower(),
            "explanation": str(data.get("explanation") or ""),
            "names": names,
            "rows": db.normalize_rows(rows),
        }

    return {"ok": False, "error": f"NL2SQL 重试 2 次仍未成功：{last_error}"}
