"""数值格式化与确定性结论生成（不编造数字，所有结论都来自查询结果）。"""
from __future__ import annotations

from app.semantic import loader


def fmt_money(v: float | None) -> str:
    if v is None:
        return "—"
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1e8:
        return f"{sign}{v / 1e8:.2f} 亿"
    if v >= 1e4:
        return f"{sign}{v / 1e4:.1f} 万"
    return f"{sign}{v:,.0f}"


def fmt_int(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{int(round(v)):,}"


def fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v * 100:+.1f}%"


def fmt_pct_plain(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.1f}%"


def fmt_by(value, form: str) -> str:
    if value is None:
        return "—"
    if form == "currency":
        return fmt_money(float(value)) + " EUR"
    if form == "int":
        return fmt_int(float(value))
    if form == "percent":
        return fmt_pct_plain(float(value))
    return str(value)


def _metric_unit(metric: str) -> str:
    return loader.load()["semantic"]["metrics"][metric]["unit"]


def summarize(plan, columns, rows) -> dict:
    """返回 {headline, bullets[], note}。

    headline 是给用户看的一句话结论；bullets 是支撑要点。
    所有数字均直接取自 rows，不做任何推测。
    """
    intent = plan.intent
    metric_cols = [c for c in columns if c.kind == "metric"]
    dim_cols = [c for c in columns if c.kind == "dim"]
    derived_cols = [c for c in columns if c.kind == "derived"]
    if not metric_cols:
        return {"headline": "没有可展示的指标。", "bullets": []}

    main = metric_cols[0]
    unit = _metric_unit(main.key)
    money = main.format == "currency"
    f = fmt_money if money else fmt_int
    idx = {c.key: i for i, c in enumerate(columns)}
    scope = plan.period_label

    bullets: list[str] = []

    if not dim_cols:
        v = rows[0][idx[main.key]]
        headline = f"{scope} {main.label.replace(f'（{unit}）', '')}为 {f(v)} {unit}。"
        for dc in derived_cols:
            dv = rows[0][idx[dc.key]]
            if dv is None:
                bullets.append(f"{dc.label}：基期无数据，无法计算。")
            else:
                arrow = "增长" if dv > 0 else "下降"
                bullets.append(
                    f"{dc.label} {fmt_pct(dv)}（基期 {plan.compare_label}）。"
                    if "同比" in dc.label else f"{dc.label} {fmt_pct(dv)}。"
                )
        return {"headline": headline, "bullets": bullets}

    # 分组结果
    total = sum((r[idx[main.key]] or 0) for r in rows)
    top = max(rows, key=lambda r: r[idx[main.key]] or 0)
    bottom = min(rows, key=lambda r: r[idx[main.key]] or 0)
    dim_label = dim_cols[0].label
    is_time_series = dim_cols[0].key in ("period", "order_year")
    k_head = f"{scope} 共 {len(rows)} 个{dim_label}，合计 {f(total)} {unit}"
    if is_time_series:
        first, last = rows[0], rows[-1]
        head = (
            f"{scope} {main.label.replace(f'（{unit}）', '')}："
            f"首期 {f(first[idx[main.key]])} → 末期 {f(last[idx[main.key]])} {unit}，"
            f"区间合计 {f(total)} {unit}，单期均值 {f(total / len(rows))} {unit}。"
        )
        peak = max(rows, key=lambda r: r[idx[main.key]] or 0)
        low = min(rows, key=lambda r: r[idx[main.key]] or 0)
        bullets.append(
            f"峰值 {f(peak[idx[main.key]])} {unit}（{peak[idx[dim_cols[0].key]]}）；"
            f"谷值 {f(low[idx[main.key]])} {unit}（{low[idx[dim_cols[0].key]]}）。"
        )
    else:
        head = (
            f"{k_head}；最高的是 {top[idx[dim_cols[0].key]]}（{f(top[idx[main.key]])} {unit}，"
            f"占 {top[idx[main.key]] / total * 100:.1f}%），最低的是 {bottom[idx[dim_cols[0].key]]}"
            f"（{f(bottom[idx[main.key]])} {unit}）。"
        )

    # Top3 明细
    if not is_time_series:
        for r in rows[:3]:
            ratio_idx = [c for c in derived_cols if c.key.endswith("_ratio")]
            ratio_txt = ""
            if ratio_idx:
                rv = r[idx[ratio_idx[0].key]]
                if rv is not None:
                    ratio_txt = f"，占 {fmt_pct_plain(rv)}"
            bullets.append(f"{r[idx[dim_cols[0].key]]}：{f(r[idx[main.key]])} {unit}{ratio_txt}")

    # 同比/环比幅度
    for dc in derived_cols:
        if "同比" not in dc.label and "环比" not in dc.label:
            continue
        vals = [(r[idx[dim_cols[0].key]], r[idx[dc.key]]) for r in rows if r[idx[dc.key]] is not None]
        if not vals:
            bullets.append(f"{dc.label}：基期数据缺失，无法计算。")
            continue
        best = max(vals, key=lambda x: x[1])
        worst = min(vals, key=lambda x: x[1])
        bullets.append(
            f"{dc.label}区间：最高 {best[0]} {fmt_pct(best[1])}，最低 {worst[0]} {fmt_pct(worst[1])}"
            f"（基期 {plan.compare_label}）。"
        )

    note = ""
    if intent.filters:
        note = "筛选条件：" + "；".join(f"{k}={v}" for k, v in intent.filters.items())
    return {"headline": head, "bullets": bullets, "note": note}
