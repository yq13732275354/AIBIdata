"""结果 → ECharts option。只产出纯 JSON 结构，格式化（万/亿、百分号）在前端做。"""
from __future__ import annotations

from app.semantic.compiler import Column, Plan

PALETTE = ["#4F8CFF", "#22D3EE", "#22C55E", "#F59E0B", "#EF4444", "#A78BFA", "#F472B6"]


def build_chart(plan: Plan, columns: list[Column], rows: list[list]) -> dict | None:
    if not rows:
        return None

    dim_cols = [c for c in columns if c.kind == "dim"]
    metric_cols = [c for c in columns if c.kind == "metric"]
    derived_cols = [c for c in columns if c.kind == "derived"]

    if plan.chart in ("kpi", "table") or not dim_cols:
        return None

    idx = {c.key: i for i, c in enumerate(columns)}
    x_key = dim_cols[0].key
    categories = [str(r[idx[x_key]]) for r in rows]

    # 分组数量太多时饼图不可读，退化成条形图（用户显式要求饼图时尊重其选择）
    chart_type = plan.chart
    if chart_type in ("pie", "donut") and len(rows) > 12 and not getattr(plan, "chart_explicit", False):
        chart_type = "bar"

    def series_of(col: Column, axis: str = "left") -> dict:
        is_line = chart_type in ("line", "area") or (chart_type == "combo" and col.kind != "metric")
        s = {
            "name": col.label,
            "type": "line" if is_line else "bar",
            "yAxisIndex": 0 if axis == "left" else 1,
            "data": [r[idx[col.key]] for r in rows],
            "smooth": chart_type in ("line", "area"),
            "symbolSize": 6,
            "barMaxWidth": 26,
        }
        if chart_type == "area":
            s["areaStyle"] = {"opacity": 0.18}
        return s

    if chart_type in ("line", "area", "combo"):
        series = [series_of(c) for c in metric_cols]
        for i, c in enumerate(derived_cols):
            s = series_of(c, "right" if c.format == "percent" else "left")
            s["type"] = "line"
            s["lineStyle"] = {"type": "dashed", "width": 1.5}
            s["itemStyle"] = {"color": PALETTE[(len(metric_cols) + i) % len(PALETTE)]}
            series.append(s)
        return {
            "backgroundColor": "transparent",
            "color": PALETTE,
            "tooltip": {"trigger": "axis", "axisPointer": {"type": "line"}},
            "legend": {"top": 4, "textStyle": {"color": "#9BA6B2"}},
            "grid": {"left": 12, "right": 12, "top": 44, "bottom": 8, "containLabel": True},
            "xAxis": {
                "type": "category",
                "data": categories,
                "axisLabel": {"color": "#7D8A99", "hideOverlap": True},
                "axisLine": {"lineStyle": {"color": "#2A3341"}},
            },
            "yAxis": [
                {
                    "type": "value",
                    "axisLabel": {"color": "#7D8A99"},
                    "splitLine": {"lineStyle": {"color": "rgba(255,255,255,0.06)"}},
                },
                {
                    "type": "value",
                    "axisLabel": {"color": "#7D8A99", "formatter": "{value}"},
                    "splitLine": {"show": False},
                    "show": bool(derived_cols),
                },
            ],
            "series": series,
        }

    if chart_type in ("pie", "donut"):
        c = metric_cols[0]
        radius = ["45%", "72%"] if chart_type == "donut" else ["38%", "66%"]
        return {
            "backgroundColor": "transparent",
            "color": PALETTE,
            "tooltip": {"trigger": "item"},
            "legend": {"type": "scroll", "top": 4, "textStyle": {"color": "#9BA6B2"}},
            "series": [
                {
                    "name": c.label,
                    "type": "pie",
                    "radius": radius,
                    "center": ["50%", "58%"],
                    "data": [{"name": cat, "value": r[idx[c.key]]} for cat, r in zip(categories, rows)],
                    "label": {"color": "#C9D1D9", "fontSize": 11, "show": len(rows) <= 12},
                    "itemStyle": {"borderColor": "#141A22", "borderWidth": 2},
                }
            ],
        }

    if chart_type == "hbar":
        c = metric_cols[0]
        return {
            "backgroundColor": "transparent",
            "color": PALETTE,
            "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
            "grid": {"left": 12, "right": 24, "top": 16, "bottom": 8, "containLabel": True},
            "xAxis": {"type": "value", "axisLabel": {"color": "#7D8A99"},
                      "splitLine": {"lineStyle": {"color": "rgba(255,255,255,0.06)"}}},
            "yAxis": {"type": "category", "data": categories, "inverse": True,
                      "axisLabel": {"color": "#7D8A99"}, "axisLine": {"lineStyle": {"color": "#2A3341"}}},
            "series": [{
                "name": c.label, "type": "bar", "barMaxWidth": 18,
                "data": [r[idx[c.key]] for r in rows],
            }],
        }

    if chart_type == "scatter":
        series = [{
            "name": c.label, "type": "scatter", "symbolSize": 9,
            "data": [r[idx[c.key]] for r in rows],
        } for c in metric_cols]
        return {
            "backgroundColor": "transparent",
            "color": PALETTE,
            "tooltip": {"trigger": "axis"},
            "legend": {"top": 4, "textStyle": {"color": "#9BA6B2"}},
            "grid": {"left": 12, "right": 16, "top": 40, "bottom": 8, "containLabel": True},
            "xAxis": {"type": "category", "data": categories,
                      "axisLabel": {"color": "#7D8A99", "interval": 0, "rotate": 30 if len(categories) > 8 else 0},
                      "axisLine": {"lineStyle": {"color": "#2A3341"}}},
            "yAxis": {"type": "value", "axisLabel": {"color": "#7D8A99"},
                      "splitLine": {"lineStyle": {"color": "rgba(255,255,255,0.06)"}}},
            "series": series,
        }

    series = [series_of(c) for c in metric_cols]
    return {
        "backgroundColor": "transparent",
        "color": PALETTE,
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "legend": {"top": 4, "textStyle": {"color": "#9BA6B2"}},
        "grid": {"left": 12, "right": 16, "top": 40, "bottom": 8, "containLabel": True},
        "xAxis": {
            "type": "category",
            "data": categories,
            "axisLabel": {"color": "#7D8A99", "interval": 0, "rotate": 30 if len(categories) > 8 else 0},
            "axisLine": {"lineStyle": {"color": "#2A3341"}},
        },
        "yAxis": {
            "type": "value",
            "axisLabel": {"color": "#7D8A99"},
            "splitLine": {"lineStyle": {"color": "rgba(255,255,255,0.06)"}},
        },
        "series": series,
    }


def build_generic_chart(names: list[str], rows: list[list], chart_type: str) -> dict | None:
    """NL2SQL 结果没有语义元数据，按列类型猜图。"""
    if not rows or len(names) < 2:
        return None
    num_idx = [i for i in range(len(names)) if all(isinstance(r[i], (int, float)) and r[i] is not None for r in rows)]
    text_idx = [i for i in range(len(names)) if i not in num_idx]
    if not num_idx or not text_idx:
        return None
    x_i, y_i = text_idx[0], num_idx[0]
    cats = [str(r[x_i]) for r in rows]
    vals = [r[y_i] for r in rows]
    if chart_type in ("pie", "donut") and len(rows) > 12:
        chart_type = "bar"
    kind = "line" if chart_type in ("line", "area") else ("pie" if chart_type in ("pie", "donut") else "bar")
    if kind == "pie":
        return {
            "backgroundColor": "transparent",
            "color": PALETTE,
            "tooltip": {"trigger": "item"},
            "legend": {"type": "scroll", "top": 4, "textStyle": {"color": "#9BA6B2"}},
            "series": [{
                "type": "pie",
                "radius": ["45%", "72%"] if chart_type == "donut" else ["38%", "66%"],
                "center": ["50%", "58%"],
                "data": [{"name": c, "value": v} for c, v in zip(cats, vals)],
                "label": {"color": "#C9D1D9", "fontSize": 11},
                "itemStyle": {"borderColor": "#141A22", "borderWidth": 2},
            }],
        }
    series = {"name": names[y_i], "type": kind, "data": vals,
              "smooth": kind == "line", "barMaxWidth": 26}
    if kind == "line" and chart_type == "area":
        series["areaStyle"] = {"opacity": 0.18}
    return {
        "backgroundColor": "transparent",
        "color": PALETTE,
        "tooltip": {"trigger": "axis"},
        "grid": {"left": 12, "right": 16, "top": 24, "bottom": 8, "containLabel": True},
        "xAxis": {
            "type": "category",
            "data": cats,
            "axisLabel": {"color": "#7D8A99", "interval": 0, "rotate": 30 if len(cats) > 8 else 0},
            "axisLine": {"lineStyle": {"color": "#2A3341"}},
        },
        "yAxis": {"type": "value", "axisLabel": {"color": "#7D8A99"},
                  "splitLine": {"lineStyle": {"color": "rgba(255,255,255,0.06)"}}},
        "series": [series],
    }
