"""规则化意图解析：时间 / 指标 / 维度 / 过滤 / 粒度 / TopN。

设计原则：能用确定性规则解析的，就不交给 LLM。LLM 只在语义层覆盖不到时兜底。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.config import settings
from app.semantic import loader


# ---------------- 期间工具 ----------------
def ym_add(period: str, delta_months: int) -> str:
    y, m = int(period[:4]), int(period[5:7])
    idx = y * 12 + (m - 1) + delta_months
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def period_to_idx(period: str) -> int:
    y, m = int(period[:4]), int(period[5:7])
    return y * 12 + (m - 1)


def idx_to_period(idx: int) -> str:
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def quarter_range(year: int, q: int) -> tuple[str, str]:
    return f"{year:04d}-{(q - 1) * 3 + 1:02d}", f"{year:04d}-{q * 3:02d}"


def half_range(year: int, half: int) -> tuple[str, str]:
    return (f"{year:04d}-01", f"{year:04d}-06") if half == 1 else (f"{year:04d}-07", f"{year:04d}-12")


@dataclass
class TimeRange:
    start: str
    end: str
    label: str
    explicit: bool = True
    granularity: str | None = None  # month / year / None

    def months(self) -> list[str]:
        return [idx_to_period(i) for i in range(period_to_idx(self.start), period_to_idx(self.end) + 1)]


@dataclass
class Intent:
    question: str
    metrics: list[str] = field(default_factory=list)
    derived: list[str] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    filters: dict[str, list[str]] = field(default_factory=dict)
    time: TimeRange | None = None
    top_n: int | None = None
    order: str = "desc"
    order_by: str = "value"  # value / yoy / mom
    chart: str | None = None  # 用户显式要求的图形：combo / bar / line / area / pie
    source: str = "semantic"

    def slots(self) -> dict:
        return {
            "metrics": self.metrics,
            "derived": self.derived,
            "group_by": self.group_by,
            "filters": self.filters,
            "time": [self.time.start, self.time.end, self.time.label] if self.time else None,
            "top_n": self.top_n,
            "order": self.order,
            "order_by": self.order_by,
            "chart": self.chart,
        }


class Clarify(Exception):
    """槽位缺失，需要向用户追问。"""

    def __init__(self, question: str, options: list[str] | None = None):
        super().__init__(question)
        self.question = question
        self.options = options or []


# ---------------- 时间解析 ----------------
def _parse_grain(text: str) -> str | None:
    sem = loader.load()["semantic"]["grain"]
    for word in sem["month"]:
        if word in text:
            return "month"
    for word in sem["year"]:
        if word in text:
            return "year"
    return None


def _parse_time(text: str) -> TimeRange:
    cfg = settings()
    ay, am = cfg["anchor_year"], cfg["anchor_month"]
    anchor = cfg["anchor_period"]
    grain = _parse_grain(text)

    if m := re.search(r"(?:最近|近)\s*(\d{1,2})\s*个?月", text):
        n = min(max(int(m.group(1)), 1), 24)
        return TimeRange(ym_add(anchor, -(n - 1)), anchor, f"近{n}个月", True, grain)

    if re.search(r"上月|上个月|前一个月", text):
        p = ym_add(anchor, -1)
        return TimeRange(p, p, f"{p[:4]}年{int(p[5:])}月（上月）", True, grain)
    if re.search(r"本月|这个月|当月|这月", text):
        return TimeRange(anchor, anchor, f"{anchor[:4]}年{int(anchor[5:])}月（本月）", True, grain)
    if re.search(r"上季度|上季", text):
        q = (am - 1) // 3 + 1
        y, q = (ay, q - 1) if q > 1 else (ay - 1, 4)
        s, e = quarter_range(y, q)
        return TimeRange(s, e, f"{y}Q{q}", True, grain)
    if re.search(r"本季度|这季度|当季|本季", text):
        q = (am - 1) // 3 + 1
        s, e = quarter_range(ay, q)
        return TimeRange(s, e, f"{ay}Q{q}", True, grain)
    if re.search(r"去年|上一年|上年", text):
        return TimeRange(f"{ay - 1:04d}-01", f"{ay - 1:04d}-12", f"{ay - 1}年", True, grain)
    if re.search(r"今年|本年|全年", text):
        return TimeRange(f"{ay:04d}-01", f"{ay:04d}-12", f"{ay}年", True, grain)

    if m := re.search(r"(\d{4})\s*年?\s*(上|下)半年", text):
        y, half = int(m.group(1)), 1 if m.group(2) == "上" else 2
        s, e = half_range(y, half)
        return TimeRange(s, e, f"{y}年{m.group(2)}半年", True, grain)
    if re.search(r"上半年|下半年", text):
        half = 1 if "上" in text else 2
        s, e = half_range(ay, half)
        return TimeRange(s, e, f"{ay}年{'上' if half == 1 else '下'}半年", True, grain)

    if m := re.search(r"(\d{4})-?[Qq]([1-4])", text):
        s, e = quarter_range(int(m.group(1)), int(m.group(2)))
        return TimeRange(s, e, f"{m.group(1)}Q{m.group(2)}", True, grain)
    if m := re.search(r"(\d{4})\s*年\s*第?\s*([一二三四]|[1-4])\s*季度", text):
        q = _cn_num(m.group(2))
        s, e = quarter_range(int(m.group(1)), q)
        return TimeRange(s, e, f"{m.group(1)}Q{q}", True, grain)

    if m := re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", text):
        y, mo = int(m.group(1)), int(m.group(2))
        p = f"{y:04d}-{mo:02d}"
        return TimeRange(p, p, f"{y}年{mo}月", True, grain)
    if m := re.search(r"(\d{4})\s*[-/]\s*(\d{1,2})(?!\d)", text):
        y, mo = int(m.group(1)), int(m.group(2))
        p = f"{y:04d}-{mo:02d}"
        return TimeRange(p, p, f"{y}年{mo}月", True, grain)

    if m := re.search(r"(\d{4})\s*年", text):
        y = int(m.group(1))
        return TimeRange(f"{y:04d}-01", f"{y:04d}-12", f"{y}年", True, grain)

    if m := re.search(r"[Qq]([1-4])|第?([一二三四])\s*季度", text):
        q = int(m.group(1)) if m.group(1) else _cn_num(m.group(2))
        s, e = quarter_range(ay, q)
        return TimeRange(s, e, f"{ay}Q{q}", True, grain)

    if m := re.search(r"(\d{1,2})\s*月", text):
        mo = int(m.group(1))
        if 1 <= mo <= 12:
            p = f"{ay:04d}-{mo:02d}"
            return TimeRange(p, p, f"{ay}年{mo}月", True, grain)

    cat = loader.load()["catalog"]
    return TimeRange(cat["period_min"], cat["period_max"], f"全部数据（{cat['period_min']}~{cat['period_max']}）", False, grain)


def recent_months(n: int = 12, granularity: str | None = None) -> TimeRange:
    anchor = settings()["anchor_period"]
    return TimeRange(ym_add(anchor, -(n - 1)), anchor, f"近{n}个月", False, granularity)


# ---------------- 维度/值解析 ----------------
NEGATIVE_WORDS = ["下降", "下滑", "下跌", "最差", "最低", "最少", "减少", "回落"]


def _cn_num(s: str) -> int:
    zh = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if "十" in s:
        a, _, b = s.partition("十")
        return zh.get(a, 1) * 10 + (zh.get(b, 0) if b else 0)
    return zh.get(s, 0)


def _match_values(text: str) -> dict[str, list[str]]:
    """在文本中定位维度值；长别名优先，避免「空调」误吃掉「移动空调」。"""
    idx = loader.load()["value_index"]
    low = text.lower()
    candidates: list[tuple[int, int, str, list[str]]] = []
    for (dim, alias), targets in idx.items():
        pos = low.find(alias)
        if pos >= 0 and targets:
            candidates.append((pos, -len(alias), dim, targets))
    candidates.sort()
    used: list[tuple[int, int]] = []
    out: dict[str, list[str]] = {}
    for pos, neg_len, dim, targets in candidates:
        ln = -neg_len
        if any(pos < e and pos + ln > s for s, e in used):
            continue
        used.append((pos, pos + ln))
        bucket = out.setdefault(dim, [])
        for t in targets:
            if t not in bucket:
                bucket.append(t)
    return out


def _detect_dims(text: str) -> list[str]:
    dim_alias = loader.load()["dim_alias"]
    low = text.lower()
    found: list[tuple[int, str]] = []
    for alias, dim in dim_alias.items():
        pos = low.find(alias)
        if pos >= 0 and all(d != dim for _, d in found):
            found.append((pos, dim))
    found.sort()
    return [d for _, d in found]


def _parse_derived(text: str) -> list[str]:
    low = text.lower()
    out = []
    for alias, key in loader.load()["derived_alias"].items():
        if alias in low and key not in out:
            out.append(key)
    return out


def _parse_topn(text: str) -> int | None:
    sem = loader.load()["semantic"]["derived"]["topn"]
    if not any(a in text.lower() for a in sem["aliases"]):
        return None
    n = None
    if m := re.search(r"top\s*(\d{1,2})", text, re.I):
        n = int(m.group(1))
    elif m := re.search(r"前\s*(\d{1,2})\s*(?:名|个|位)?", text):
        n = int(m.group(1))
    elif m := re.search(r"(\d{1,2})\s*(?:个|名|位|大)", text):
        n = int(m.group(1))
    elif m := re.search(r"前([一二三四五六七八九十]{1,2})", text):
        n = _cn_num(m.group(1))
    if not n:
        n = sem["default_n"]
    return max(1, min(int(n), sem["max_n"]))


def _parse_chart(text: str) -> str | None:
    """识别用户显式要求的图形形式。组合图优先级最高（同时提到柱和线）。"""
    low = text.lower()
    has_bar = any(w in low for w in ("柱状图", "柱形图", "柱状", "柱形", "条形图", "直方图"))
    has_line = any(w in low for w in ("折线图", "折线", "曲线图", "曲线"))
    if any(w in low for w in ("组合图", "组合图表", "柱线组合", "柱状图加折线", "柱形图加折线")):
        return "combo"
    if has_bar and has_line:
        return "combo"
    if any(w in low for w in ("环形图", "圆环图", "甜甜圈", "环图")):
        return "donut"
    if any(w in low for w in ("饼图", "饼状图")):
        return "pie"
    if any(w in low for w in ("散点图", "散点")):
        return "scatter"
    if any(w in low for w in ("以表格", "用表格", "表格形式", "表格展示", "只看表格", "仅表格")):
        return "table"
    if any(w in low for w in ("面积图", "区域图")):
        return "area"
    if any(w in low for w in ("条形图", "横向柱状图", "横向柱形图")):
        return "hbar"
    if has_line:
        return "line"
    if has_bar:
        return "bar"
    return None


# ---------------- 主入口 ----------------
def parse(question: str, slots: dict | None = None) -> Intent:
    """解析问题；slots 为上一轮已确认的槽位，用于多轮继承。"""
    text = question.strip()
    low = text.lower()
    idx = loader.load()

    metrics = [k for a, k in idx["metric_alias"].items() if a in low]
    metrics = list(dict.fromkeys(metrics))
    derived = _parse_derived(text)

    values = _match_values(text)
    dims_named = _detect_dims(text)
    # 说了值但没说维度名（如「西班牙」「移动空调」）也要落到对应维度上
    dims_all = dims_named + [d for d in values if d not in dims_named]

    filters: dict[str, list[str]] = {}
    group_by: list[str] = []
    for dim in dims_all:
        if dim in values:
            filters[dim] = values[dim]
        else:
            group_by.append(dim)

    top_n = _parse_topn(text)
    time = _parse_time(text)
    chart = _parse_chart(text)

    order, order_by = "desc", "value"
    if any(w in low for w in NEGATIVE_WORDS):
        order = "asc"

    # 多轮继承：本轮没提到的槽位沿用上一轮
    if slots:
        if not metrics and slots.get("metrics"):
            metrics = list(slots["metrics"])
        if not group_by and not filters and slots.get("group_by"):
            group_by = list(slots["group_by"])
        if not filters and slots.get("filters"):
            filters = dict(slots["filters"])
        if not time.explicit and slots.get("time"):
            s, e, label = slots["time"]
            time = TimeRange(s, e, label, False, time.granularity)

    # 缺省时间：涉及同比/环比时锚定近 12 个月，否则用全量
    if not time.explicit and ("yoy" in derived or "mom" in derived or "为什么" in text or "原因" in text):
        gran = time.granularity or ("month" if ("为什么" in text or "原因" in text) else None)
        time = recent_months(12, gran)

    has_metric_signal = bool(metrics)
    if not metrics:
        metrics = ["sales_amount"]

    # 时间粒度
    if time.granularity == "year":
        group_time = "year"
    elif time.granularity == "month":
        group_time = "month"
    else:
        group_time = None

    # TopN / 占比 必须有分组维度
    if (top_n or "ratio" in derived) and not group_by:
        raise Clarify(
            "想按哪个维度看？",
            ["按品类", "按平台", "按国家"],
        )

    # 有同比且按维度分组时，排序可能按增速
    if group_by and "yoy" in derived:
        if any(w in low for w in ["增长最快", "增长最多", "增速最快", "最快"]):
            order_by, order = "yoy", "desc"
        elif any(w in low for w in ["下滑", "下降", "最差", "跌幅"]):
            order_by, order = "yoy", "asc"

    intent = Intent(
        question=text,
        metrics=metrics[:2],
        derived=[d for d in derived if d in ("yoy", "mom", "ratio", "topn")],
        group_by=group_by,
        filters=filters,
        time=time,
        top_n=top_n,
        order=order,
        order_by=order_by,
        chart=chart,
    )
    intent.time.granularity = group_time

    # 没有任何业务信号时追问，而不是默认给一个全量销售额
    if not dims_all and not derived and not time.explicit and not has_metric_signal:
        raise Clarify("想查哪方面的数据？", ["销售额趋势", "Top 品类", "各平台销售额", "同比变化"])

    return intent
