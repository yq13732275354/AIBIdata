"""语义层编译器：Intent -> 确定性 SQL。

不依赖 LLM。所有口径（同比/环比/占比/TopN）都在这里生成，保证可复现。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.semantic import intent as it
from app.semantic import loader


class NoData(Exception):
    """请求的口径在当前数据集上不可计算（如 2024 年的同比）。"""

    def __init__(self, message: str, suggestion: str = ""):
        super().__init__(message)
        self.message = message
        self.suggestion = suggestion


@dataclass
class Column:
    key: str
    label: str
    kind: str  # dim / metric / derived
    format: str = "number"  # number / currency / int / percent / text


@dataclass
class Plan:
    sql: str
    intent: it.Intent
    columns: list[Column] = field(default_factory=list)
    chart: str = "table"
    chart_explicit: bool = False
    notes: list[str] = field(default_factory=list)
    compare_label: str = ""
    period_label: str = ""


def _sql_str(v: str) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def _filters_sql(filters: dict[str, list[str]]) -> str:
    sem = loader.load()["semantic"]
    parts = []
    for dim, vals in filters.items():
        col = sem["dimensions"][dim]["column"]
        parts.append(f"{col} IN ({', '.join(_sql_str(v) for v in vals)})")
    return (" AND " + " AND ".join(parts)) if parts else ""


def _dims_of(intent: it.Intent) -> list[tuple[str, str, str]]:
    """[(输出列名, SQL 表达式, 中文标签)]，按展示顺序排列。"""
    sem = loader.load()["semantic"]
    out: list[tuple[str, str, str]] = []
    for dim in intent.group_by:
        cfg = sem["dimensions"][dim]
        out.append((dim, cfg["column"], cfg["label"]))
    grain = intent.time.granularity if intent.time else None
    if grain == "month":
        out.append(("period", "period", "月份"))
    elif grain == "year":
        out.append(("order_year", "order_year", "年份"))
    return out


def _check_feasible(intent: it.Intent) -> tuple[set[str], list[str]]:
    """口径可行性校验；不可行时抛出 NoData，并给出可执行的替代建议。"""
    cat = loader.load()["catalog"]
    data_min, data_max = cat["period_min"], cat["period_max"]
    derived = set(intent.derived)
    notes: list[str] = []
    assert intent.time is not None
    start, end = intent.time.start, intent.time.end

    if "yoy" in derived:
        prev_start = it.ym_add(start, -12)
        if prev_start < data_min:
            raise NoData(
                f"「{intent.time.label}」的同比不可计算：同比需要上一年同期，"
                f"而数据集只覆盖 {data_min}~{data_max}，缺少更早的基期。",
                "可以改查 2025 年（或 2025 年某个月）的同比，其基期正好是 2024 年。",
            )
        notes.append(f"同比基期 {prev_start} ~ {it.ym_add(end, -12)}")

    if "mom" in derived:
        if intent.time.granularity == "year":
            raise NoData(
                "环比按「年」展开没有意义：年度之间应看同比。",
                "改成按月份展开（如「2025 年各月销售额环比」），或直接看同比。",
            )
        if it.ym_add(start, -1) < data_min:
            raise NoData(
                f"「{start}」的环比不可计算：它是数据集第一个月，没有上一个期间。",
                "可以改查 2024-02 之后的月份，或改看同比。",
            )
        if start != end and not intent.time.granularity:
            raise NoData(
                "环比需要单月或逐月粒度：当前是跨月区间且没有按月展开，没有可对应的上一期间。",
                "加上「各月/每月」按月展开，或直接指定单个月份。",
            )
        notes.append("环比基期 上一期间")

    return derived, notes


def compile_plan(intent: it.Intent) -> Plan:
    sem = loader.load()["semantic"]
    assert intent.time is not None
    start, end = intent.time.start, intent.time.end
    derived, notes = _check_feasible(intent)

    dims = _dims_of(intent)
    # 用户要求趋势类图表（折线/面积/组合），但没指定粒度且区间跨多月时，自动按月展开
    if (not dims and intent.chart in ("line", "area", "combo")
            and it.period_to_idx(end) - it.period_to_idx(start) >= 1):
        dims = [("period", "period", "月份")]
    dims_sql = "".join(f"{expr} AS {key}, " for key, expr, _ in dims)
    group_clause = f" GROUP BY {', '.join(str(i + 1) for i in range(len(dims)))}" if dims else ""

    where = f"period BETWEEN {_sql_str(start)} AND {_sql_str(end)}{_filters_sql(intent.filters)}"

    metrics_sql = ", ".join(f"{sem['metrics'][m]['agg']} AS {m}" for m in intent.metrics)
    primary = intent.metrics[0]

    needs_compare = bool({"yoy", "mom"} & derived)
    if needs_compare:
        shift = 12 if "yoy" in derived else 1
        p_start, p_end = it.ym_add(start, -shift), it.ym_add(end, -shift)
        prev_metrics = ", ".join(f"{sem['metrics'][m]['agg']} AS prev_{m}" for m in intent.metrics)

        join_conds = []
        for key, _, _ in dims:
            if key == "period":
                join_conds.append(
                    f"p.period = strftime(CAST(cur.period || '-01' AS DATE) - INTERVAL '{shift}' MONTH, '%Y-%m')"
                )
            elif key == "order_year":
                join_conds.append(f"p.order_year = cur.order_year - {shift // 12}")
            else:
                join_conds.append(f"p.{key} = cur.{key}")
        join_sql = "LEFT JOIN prev p ON " + (" AND ".join(join_conds) if join_conds else "1 = 1")

        select_parts = [f"cur.{key}" for key, _, _ in dims] + [f"cur.{m}" for m in intent.metrics]
        for m in intent.metrics:
            if "yoy" in derived:
                select_parts.append(f"(cur.{m} - p.prev_{m}) / NULLIF(p.prev_{m}, 0) AS {m}_yoy")
            if "mom" in derived:
                select_parts.append(f"(cur.{m} - p.prev_{m}) / NULLIF(p.prev_{m}, 0) AS {m}_mom")

        core = (
            f"WITH cur AS (SELECT {dims_sql}{metrics_sql} FROM sales WHERE {where}{group_clause}), "
            f"prev AS (SELECT {dims_sql}{prev_metrics} FROM sales "
            f"WHERE period BETWEEN {_sql_str(p_start)} AND {_sql_str(p_end)}{_filters_sql(intent.filters)}{group_clause}) "
            f"SELECT {', '.join(select_parts)} FROM cur {join_sql}"
        )
    else:
        core = f"SELECT {dims_sql}{metrics_sql} FROM sales WHERE {where}{group_clause}"

    # 占比：分母为整个结果集（窗口函数），在 LIMIT 之前计算
    if "ratio" in derived:
        if dims:
            core = (
                f"SELECT *, {primary} / NULLIF(SUM({primary}) OVER (), 0) AS {primary}_ratio FROM ({core}) t"
            )
        else:
            notes.append("占比需要分组维度，本次已忽略占比。")

    # 排序 / TopN
    if dims:
        dim_keys = [k for k, _, _ in dims]
        time_dim = "period" if "period" in dim_keys else ("order_year" if "order_year" in dim_keys else None)
        if time_dim and not intent.top_n:
            # 时间序列必须按时间升序，否则趋势图会乱序
            core += f" ORDER BY {time_dim} ASC"
        else:
            order_col = primary
            if "yoy" in derived and intent.order_by == "yoy":
                order_col = f"{primary}_yoy"
            elif "mom" in derived and intent.order_by == "mom":
                order_col = f"{primary}_mom"
            direction = "DESC" if intent.order == "desc" else "ASC"
            core += f" ORDER BY {order_col} {direction} NULLS LAST"
        core += f" LIMIT {int(intent.top_n)}" if intent.top_n else " LIMIT 1000"

    # 列描述
    cols: list[Column] = []
    for key, _, label in dims:
        cols.append(Column(key=key, label=label, kind="dim", format="text"))
    for m in intent.metrics:
        cfg = sem["metrics"][m]
        cols.append(Column(key=m, label=f"{cfg['label']}（{cfg['unit']}）", kind="metric", format=cfg["format"]))
    for m in intent.metrics:
        label = sem["metrics"][m]["label"]
        if "yoy" in derived:
            cols.append(Column(key=f"{m}_yoy", label=f"{label}同比", kind="derived", format="percent"))
        if "mom" in derived:
            cols.append(Column(key=f"{m}_mom", label=f"{label}环比", kind="derived", format="percent"))
        if "ratio" in derived and dims:
            cols.append(Column(key=f"{m}_ratio", label=f"{label}占比", kind="derived", format="percent"))

    dim_keys = [k for k, _, _ in dims]
    has_time_dim = "period" in dim_keys or "order_year" in dim_keys
    requested = intent.chart
    if not dims:
        chart = "kpi"
    elif requested == "combo":
        # 组合图：主指标柱形 + 派生指标（同比/环比）折线
        chart = "combo" if ("yoy" in derived or "mom" in derived) else "bar"
    elif requested == "bar":
        chart = "combo" if ("yoy" in derived or "mom" in derived) else "bar"
    elif requested in ("line", "area", "hbar", "donut", "scatter"):
        chart = requested
    elif requested == "table":
        chart = "table"
    elif requested == "pie":
        chart = "pie" if (len(dim_keys) == 1 and not has_time_dim) else "bar"
    elif has_time_dim:
        chart = "line"
    elif "ratio" in derived and len(dim_keys) == 1:
        chart = "pie"
    else:
        chart = "bar"

    compare_label = ""
    if "yoy" in derived:
        compare_label = f"{it.ym_add(start, -12)} ~ {it.ym_add(end, -12)}"
    elif "mom" in derived:
        compare_label = f"{it.ym_add(start, -1)} ~ {it.ym_add(end, -1)}"

    return Plan(
        sql=core,
        intent=intent,
        columns=cols,
        chart=chart,
        chart_explicit=bool(requested),
        notes=notes,
        compare_label=compare_label,
        period_label=intent.time.label,
    )
