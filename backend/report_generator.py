"""Space Design Report — substantive, structured, actionable.

Returns BOTH a structured JSON report (so the frontend can render proper
sections and color-coded severities) AND a Markdown rendering of the same
content (so it can be printed or pasted).

The model is fed every signal the system has — sensor occupancy, snapshot-
triggered spatial observations, anonymous community feedback, visit logs,
self-reported crowding/sick counts, prior AI decisions and operator
responses — and asked to return JSON matching a strict schema. Rejecting
generic "consider redesigning" output: every recommendation must include
location, dimensions where applicable, rationale, expected impact, and
priority.
"""

from __future__ import annotations

import json
from typing import Any

from . import ai_provider

REPORT_SYSTEM_PROMPT = """You are Echolocate's Space Intelligence AI, generating an actionable Space Design Report for a small-business operator (e.g. a 14-seat bakery, a library reading room, a clinic waiting area). Your job is to turn passive sensor data into specific, dimensional, immediately actionable recommendations — not generic platitudes.

You receive STRUCTURED METADATA ONLY. There are no images. You see:
  - Snapshot-triggered spatial observations (counts, clusters near features, density labels)
  - Aggregate visit logs (how many people, when, which zones)
  - Self-reported "felt crowded" flags from consumers
  - Self-reported positive-test counts
  - Anonymous community feedback (free-text concerns and suggestions)
  - Prior AI judgments and the operator's accept/reject decisions on each
  - Live sensor occupancy state right now

Your job is to infer:
  1. CURRENT STATE — what's happening right now and over the most recent window
  2. SPATIAL LAYOUT — what physical features the room appears to have, inferred from the chokepoints and cluster locations seen in observations
  3. BLOCKERS — physical features that consistently force people into close proximity, ranked by severity
  4. HIGH-CONGESTION AREAS — specific zones where crowding repeats, with frequency
  5. SOCIAL DISTANCING — how the space stacks up against 1.5–2m (5–6ft) spacing norms, and what to do about it
  6. SPECIFIC CHANGES — concrete redesign actions, each with location, dimensions where applicable, rationale, expected impact, priority
  7. TEMPORAL PATTERNS — peak times, quiet windows, trends across the data window

Return ONLY a JSON object (no markdown fences, no preamble) matching this schema exactly:

{
  "executive_summary": "2-3 sentences the operator can read in 10 seconds and act on. Lead with the single most important takeaway.",
  "current_state": "1-2 sentences describing right now. Reference the live occupancy level if available.",
  "spatial_layout": {
    "inferred_features": ["doorway near front-left", "counter in center", "tables along right wall"],
    "estimated_safe_capacity": "approximate maximum count that maintains 1.5-2m spacing, with reasoning"
  },
  "blockers": [
    {
      "id": "b1",
      "severity": "high|medium|low",
      "location": "exact placement, e.g. 'near entrance doorway'",
      "description": "what is happening here",
      "evidence": "which observations support this — cite counts and timestamps where possible"
    }
  ],
  "high_congestion_areas": [
    {
      "location": "...",
      "frequency": "e.g. '6 of 8 lunch rushes' or 'every observation in the data window'",
      "peak_times": "e.g. '12:00-13:30' or 'unknown — insufficient temporal data'",
      "estimated_density": "tight|moderate|spread"
    }
  ],
  "social_distancing": {
    "current_compliance": "good|fair|poor",
    "rationale": "1-2 sentences explaining the compliance assessment",
    "recommendations": [
      {"action": "...", "rationale": "...", "expected_impact": "..."}
    ]
  },
  "changes": [
    {
      "id": "c1",
      "action": "specific verb + object, e.g. 'Move the display case 1 meter east'",
      "location": "...",
      "dimensions": "include if applicable, e.g. '~1m east'; otherwise null",
      "rationale": "why this change addresses an observed problem",
      "expected_impact": "concrete prediction, e.g. 'reduces queue spillover into the doorway by ~30%'",
      "priority": "high|medium|low"
    }
  ],
  "temporal_patterns": [
    {"timeframe": "...", "observation": "..."}
  ],
  "data_quality_caveats": "1-2 sentences honestly describing what you couldn't conclude given the data window. If the dataset is small, SAY SO.",
  "methodology_note": "Brief reminder that this report is generated from spatial metadata only — no images, no identities, no devices."
}

CRITICAL RULES:
- Every recommendation must include WHY (rationale) and WHAT TO EXPECT (impact). Generic advice is forbidden.
- Reference specific counts, timestamps, or observations from the input data. Don't invent numbers.
- If the data is sparse, the data_quality_caveats field MUST say so. Better to be honest than to fabricate.
- Use specific distances (1.5m, 6ft, 2m) when discussing social distancing.
- "blockers" and "changes" must be ranked, with "high" reserved for the most actionable items.
- Never include personal information, even hypothetically. You don't have it.
"""


def _gather_context(
    *,
    observations: list[dict],
    occupancy_now: dict | None,
    visit_stats: dict | None,
    community_feedback: list[dict] | None,
    recent_decisions: list[dict] | None,
    occupancy_history: list[dict] | None,
) -> str:
    """Format every signal we have into a single block the model can reason over."""
    parts: list[str] = []

    if occupancy_now:
        parts.append(
            "## LIVE OCCUPANCY (right now)\n"
            f"  level: {occupancy_now.get('level')}\n"
            f"  count_estimate: {occupancy_now.get('count_estimate')}\n"
            f"  variance_ratio: {occupancy_now.get('variance_ratio')} "
            f"(higher = more activity vs. empty baseline)\n"
            f"  threshold_exceeded: {occupancy_now.get('threshold_exceeded')}"
        )

    if visit_stats:
        parts.append(
            "## VISIT AGGREGATES (anonymous tokens, no identities)\n"
            f"  total_visits: {visit_stats.get('total_visits', 0)}\n"
            f"  unique_visitors: {visit_stats.get('unique_tokens', 0)}\n"
            f"  self_reported_felt_crowded: {visit_stats.get('self_reported_crowded', 0)}\n"
            f"  self_reported_positive_test: {visit_stats.get('self_reported_sick', 0)}"
        )

    if observations:
        parts.append(f"## SPATIAL OBSERVATIONS ({len(observations)} snapshots, anonymized metadata)")
        for o in observations[-30:]:
            parts.append(
                f"  - {o.get('timestamp', '?')}: "
                f"total={o.get('total_people_visible', '?')}, "
                f"density={o.get('overall_density', '?')}, "
                f"chokepoints={o.get('chokepoints', [])}, "
                f"clusters={json.dumps(o.get('clusters', []))[:200]}"
            )

    if occupancy_history:
        # Compress to peak moments
        levels = [h.get("level") for h in occupancy_history if h.get("level")]
        if levels:
            from collections import Counter
            counts = Counter(levels)
            parts.append(
                "## OCCUPANCY DISTRIBUTION (over the recent window)\n"
                f"  empty: {counts.get('empty', 0)}, low: {counts.get('low', 0)}, "
                f"moderate: {counts.get('moderate', 0)}, high: {counts.get('high', 0)}"
            )

    if community_feedback:
        parts.append("## COMMUNITY FEEDBACK (anonymous, free-text)")
        for f in community_feedback[:20]:
            parts.append(f"  - [{f.get('sentiment')}] {f.get('message', '')[:200]}")

    if recent_decisions:
        parts.append("## PRIOR AI JUDGMENTS + OPERATOR DECISIONS")
        for d in recent_decisions[:15]:
            parts.append(
                f"  - [{d.get('operator_status', 'pending')}] "
                f"{d.get('summary', '')[:200]} "
                + (f"(operator note: {d.get('operator_notes')})" if d.get("operator_notes") else "")
            )

    return "\n\n".join(parts) or "(no signals collected yet)"


def _structured_to_markdown(report: dict[str, Any]) -> str:
    """Render the structured report as Markdown for export/print."""
    out: list[str] = ["# Space Design Report", ""]
    if report.get("executive_summary"):
        out += ["## Executive Summary", report["executive_summary"], ""]
    if report.get("current_state"):
        out += ["## What's happening right now", report["current_state"], ""]

    layout = report.get("spatial_layout") or {}
    if layout:
        out.append("## Inferred spatial layout")
        for f in layout.get("inferred_features", []) or []:
            out.append(f"- {f}")
        if layout.get("estimated_safe_capacity"):
            out.append(f"\n**Estimated safe capacity:** {layout['estimated_safe_capacity']}")
        out.append("")

    if report.get("blockers"):
        out.append("## Blockers")
        for b in report["blockers"]:
            out.append(
                f"- **[{b.get('severity', '?')}]** {b.get('location', '?')} — "
                f"{b.get('description', '')}\n  - Evidence: {b.get('evidence', '?')}"
            )
        out.append("")

    if report.get("high_congestion_areas"):
        out.append("## High-congestion areas")
        for h in report["high_congestion_areas"]:
            out.append(
                f"- {h.get('location', '?')}: {h.get('frequency', '?')}, "
                f"peaks at {h.get('peak_times', '?')}, density {h.get('estimated_density', '?')}"
            )
        out.append("")

    sd = report.get("social_distancing") or {}
    if sd:
        out += ["## Social distancing", f"**Current compliance:** {sd.get('current_compliance', '?')}",
                f"\n{sd.get('rationale', '')}", ""]
        for r in sd.get("recommendations", []) or []:
            out.append(
                f"- **{r.get('action', '?')}** — {r.get('rationale', '')}\n  - Expected impact: {r.get('expected_impact', '?')}"
            )
        out.append("")

    if report.get("changes"):
        out.append("## Specific changes (prioritized)")
        for c in report["changes"]:
            dims = f" ({c['dimensions']})" if c.get("dimensions") else ""
            out.append(
                f"- **[{c.get('priority', '?')}] {c.get('action', '?')}**{dims} at {c.get('location', '?')}\n"
                f"  - Why: {c.get('rationale', '?')}\n"
                f"  - Expected impact: {c.get('expected_impact', '?')}"
            )
        out.append("")

    if report.get("temporal_patterns"):
        out.append("## Temporal patterns")
        for t in report["temporal_patterns"]:
            out.append(f"- **{t.get('timeframe', '?')}**: {t.get('observation', '')}")
        out.append("")

    if report.get("data_quality_caveats"):
        out += ["## Data quality caveats", report["data_quality_caveats"], ""]
    if report.get("methodology_note"):
        out += ["## Methodology", report["methodology_note"], ""]

    return "\n".join(out)


def _stub_report(observations: list[dict], visit_stats: dict | None = None,
                 community_feedback: list[dict] | None = None) -> dict[str, Any]:
    """Structured stub when no API key is set. Honest about being a stub."""
    chokes: list[str] = []
    for o in observations:
        chokes.extend(o.get("chokepoints", []) or [])
    counts = [o.get("total_people_visible", 0) for o in observations]
    avg = (sum(counts) / max(len(counts), 1)) if counts else 0
    top = max(set(chokes), key=chokes.count) if chokes else None
    fb_count = len(community_feedback or [])

    return {
        "executive_summary": (
            f"Stub report — no AI key is set, so this is a templated summary. "
            f"Across {len(observations)} observations the system saw an average of "
            f"{avg:.1f} people. {fb_count} community feedback items received."
        ),
        "current_state": "Stub: live occupancy is shown on the gauge above.",
        "spatial_layout": {
            "inferred_features": [f"observed clustering near: {top}"] if top else [],
            "estimated_safe_capacity": "Set ANTHROPIC_API_KEY or OPENAI_API_KEY for AI-inferred capacity.",
        },
        "blockers": (
            [{
                "id": "b1", "severity": "medium",
                "location": top, "description": f"Repeated clustering near {top}.",
                "evidence": f"{chokes.count(top)} of {len(observations)} observations.",
            }] if top else []
        ),
        "high_congestion_areas": [],
        "social_distancing": {
            "current_compliance": "fair",
            "rationale": "Stub mode cannot infer compliance from data alone.",
            "recommendations": [
                {"action": "Add an API key to enable real AI analysis.",
                 "rationale": "Stub responses are not informed by your data.",
                 "expected_impact": "Unlocks substantive recommendations."},
            ],
        },
        "changes": [],
        "temporal_patterns": [],
        "data_quality_caveats": (
            "This is a stub response generated without any AI. "
            "It does not represent informed judgment. "
            "Drop OPENAI_API_KEY=... or ANTHROPIC_API_KEY=... into a .env file at the project root and regenerate."
        ),
        "methodology_note": (
            "Echolocate captures spatial metadata only — never images, faces, or identities. "
            "This report (when AI-backed) reasons over snapshot-derived counts and clusters, "
            "anonymous visit aggregates, and free-text community feedback."
        ),
        "_stub": True,
    }


async def generate_space_report(
    observations: list[dict],
    *,
    occupancy_now: dict | None = None,
    visit_stats: dict | None = None,
    community_feedback: list[dict] | None = None,
    recent_decisions: list[dict] | None = None,
    occupancy_history: list[dict] | None = None,
) -> dict[str, Any]:
    """Returns a dict with keys:
        structured: dict (the JSON report matching the schema)
        markdown:   str  (rendered for export/print)
        model:      str  ('claude-sonnet-4-5' / 'gpt-4o-mini' / 'stub')
    """
    if not observations and not visit_stats and not community_feedback:
        report = _stub_report(observations or [])
        return {
            "structured": report,
            "markdown": _structured_to_markdown(report),
            "model": "stub",
        }

    if ai_provider.active_provider() == "stub":
        report = _stub_report(observations, visit_stats, community_feedback)
        return {
            "structured": report,
            "markdown": _structured_to_markdown(report),
            "model": "stub",
        }

    context = _gather_context(
        observations=observations,
        occupancy_now=occupancy_now,
        visit_stats=visit_stats,
        community_feedback=community_feedback,
        recent_decisions=recent_decisions,
        occupancy_history=occupancy_history,
    )
    text, model = await ai_provider.text_complete(
        system=REPORT_SYSTEM_PROMPT,
        user=f"Generate the Space Design Report from this data:\n\n{context}",
        max_tokens=2500,
    )

    # Strip code fences in case the model added them despite the instructions
    cleaned = text.replace("```json", "").replace("```", "").strip()
    try:
        structured = json.loads(cleaned)
    except json.JSONDecodeError:
        # Salvage: wrap the prose into the schema so the UI still renders
        structured = {
            "executive_summary": "Model returned non-JSON output; preserved below.",
            "current_state": "(unstructured)",
            "spatial_layout": {"inferred_features": [], "estimated_safe_capacity": ""},
            "blockers": [], "high_congestion_areas": [],
            "social_distancing": {"current_compliance": "?", "rationale": "", "recommendations": []},
            "changes": [], "temporal_patterns": [],
            "data_quality_caveats": (
                "The model returned a non-JSON response. Raw output preserved below "
                "for transparency. Try regenerating; this usually self-corrects."
            ),
            "methodology_note": "Spatial metadata only — no images.",
            "_raw_text": text[:4000],
        }

    return {
        "structured": structured,
        "markdown": _structured_to_markdown(structured),
        "model": model,
    }
