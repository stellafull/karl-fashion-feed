"""Shared runtime facet contract for story and digest stages."""

from __future__ import annotations


RUNTIME_FACETS = frozenset(
    {
        "runway_series",
        "street_style",
        "trend_summary",
        "brand_market",
    }
)

RUNTIME_FACET_DESCRIPTIONS = {
    "runway_series": "秀场发布、系列解读、造型看点、季节性走秀报道",
    "street_style": "街拍、场外造型、观众穿搭、城市风格观察",
    "trend_summary": "跨品牌或跨故事的趋势提炼、审美主题、风格信号总结",
    "brand_market": "品牌广告、产品发布、联名、组织与商业动作、市场层面动态",
}
