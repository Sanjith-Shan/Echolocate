"""Structured Space Design Report — covers shape, schema, and rendering.

These tests run with no API key (stub mode) so they're free and fast. The
schema and rendering must work in stub mode the same as in live mode —
that's the contract the frontend depends on.
"""

import asyncio
import os

from backend import report_generator


def _stub_env():
    saved = {k: os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")}
    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("OPENAI_API_KEY", None)
    return saved


def _restore_env(saved):
    for k, v in saved.items():
        if v is None: os.environ.pop(k, None)
        else: os.environ[k] = v


def _sample_observations():
    return [
        {
            "timestamp": "2026-05-09T12:15:00Z",
            "total_people_visible": 4,
            "overall_density": "tight",
            "spatial_issue": "Cluster near doorway",
            "chokepoints": ["doorway"],
            "clusters": [{"region": "left front", "near_feature": "doorway", "count": 4}],
        },
        {
            "timestamp": "2026-05-09T12:22:00Z",
            "total_people_visible": 6,
            "overall_density": "tight",
            "spatial_issue": "Bottleneck at counter",
            "chokepoints": ["counter", "doorway"],
            "clusters": [{"region": "center", "near_feature": "counter", "count": 6}],
        },
    ]


def test_report_returns_structured_and_markdown():
    saved = _stub_env()
    try:
        out = asyncio.run(report_generator.generate_space_report(
            _sample_observations(),
            visit_stats={"total_visits": 12, "unique_tokens": 8,
                          "self_reported_crowded": 5, "self_reported_sick": 0},
            community_feedback=[{"sentiment": "concern",
                                  "message": "Lunch line backs to the door"}],
        ))
        assert "structured" in out
        assert "markdown" in out
        assert "model" in out
        assert isinstance(out["structured"], dict)
        assert isinstance(out["markdown"], str)
        assert "# Space Design Report" in out["markdown"]
    finally:
        _restore_env(saved)


def test_structured_report_has_required_sections():
    saved = _stub_env()
    try:
        out = asyncio.run(report_generator.generate_space_report(_sample_observations()))
        s = out["structured"]
        # The schema fields the frontend uses to render
        for key in ("executive_summary", "current_state", "spatial_layout",
                    "blockers", "high_congestion_areas", "social_distancing",
                    "changes", "temporal_patterns",
                    "data_quality_caveats", "methodology_note"):
            assert key in s, f"missing key {key!r} in structured report"
    finally:
        _restore_env(saved)


def test_stub_report_is_clearly_labeled():
    """Stub responses must be honest — never pass off canned text as AI."""
    saved = _stub_env()
    try:
        out = asyncio.run(report_generator.generate_space_report(_sample_observations()))
        assert out["model"] == "stub"
        assert out["structured"].get("_stub") is True
        # The caveats field must mention that this is a stub / no key
        caveats = out["structured"]["data_quality_caveats"].lower()
        assert "stub" in caveats or "api" in caveats or "key" in caveats
    finally:
        _restore_env(saved)


def test_empty_input_returns_stub_with_caveat():
    saved = _stub_env()
    try:
        out = asyncio.run(report_generator.generate_space_report([]))
        # Even with nothing, the schema is still complete (frontend renders
        # an empty-but-valid card)
        assert "structured" in out
        assert "markdown" in out
        assert out["structured"].get("_stub") is True
    finally:
        _restore_env(saved)


def test_markdown_render_matches_structured():
    saved = _stub_env()
    try:
        out = asyncio.run(report_generator.generate_space_report(_sample_observations()))
        s = out["structured"]
        md = out["markdown"]
        # Spot-check that the executive summary made it in
        if s.get("executive_summary"):
            assert s["executive_summary"][:30] in md
    finally:
        _restore_env(saved)
