"""加载语义层配置，构建别名索引与值域候选召回。"""
from __future__ import annotations

import json
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def load() -> dict:
    semantic = yaml.safe_load((BASE / "semantic.yaml").read_text(encoding="utf-8"))
    synonyms = yaml.safe_load((BASE / "synonyms.yaml").read_text(encoding="utf-8")) or {}
    catalog = json.loads((BASE / "values.json").read_text(encoding="utf-8"))

    # 指标别名 → 指标 key
    metric_alias = {}
    for key, m in semantic["metrics"].items():
        for a in m.get("aliases", []) + [m["label"], key]:
            metric_alias[a.lower()] = key

    derived_alias = {}
    for key, d in semantic["derived"].items():
        for a in d.get("aliases", []) + [d["label"], key]:
            derived_alias[a.lower()] = key

    dim_alias = {}
    for key, d in semantic["dimensions"].items():
        for a in d.get("aliases", []) + [d["label"], key]:
            dim_alias[a.lower()] = key

    # 值域别名 → 库内真实值
    value_index: dict[tuple[str, str], list[str]] = {}
    values_by_dim = {
        "platform": catalog["platform"],
        "country": catalog["country"],
        "category": catalog["category"],
    }
    for dim, vals in values_by_dim.items():
        for v in vals:
            value_index[(dim, v.lower())] = [v]
    for dim, mapping in (synonyms or {}).items():
        for alias, targets in (mapping or {}).items():
            if not targets:
                continue
            value_index[(dim, alias.lower())] = [t for t in targets if t in values_by_dim[dim]]

    return {
        "semantic": semantic,
        "catalog": catalog,
        "values_by_dim": values_by_dim,
        "metric_alias": metric_alias,
        "derived_alias": derived_alias,
        "dim_alias": dim_alias,
        "value_index": value_index,
    }


def canonical_values(dim: str) -> list[str]:
    return load()["values_by_dim"].get(dim, [])


def recall_values(dim: str, text: str, top_k: int = 8) -> list[str]:
    """当同义词表没命中时，用相似度在值域里召回候选，避免把 143 个品类全塞进 prompt。"""
    q = (text or "").strip().lower()
    if not q:
        return []
    vals = canonical_values(dim)
    scored = []
    for v in vals:
        vl = v.lower()
        if q and (q in vl or vl in q):
            score = 1.0
        else:
            score = SequenceMatcher(None, q, vl).ratio()
        scored.append((score, v))
    scored.sort(reverse=True)
    return [v for s, v in scored[:top_k] if s >= 0.35]
