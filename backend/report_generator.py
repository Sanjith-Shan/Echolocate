"""Space Design Report generation. Uses whichever AI provider is configured."""

from __future__ import annotations

import json

from . import ai_provider

REPORT_SYSTEM_PROMPT = """You are Echolocate's Space Intelligence AI. You analyze accumulated spatial metadata from a public space monitoring system to generate Space Design Reports for building operators.

You receive a collection of spatial observations — each one is structured metadata from a brief camera snapshot that was immediately deleted. You never see images. You only see data like:
- "12:15 PM: 4 people clustered near entrance doorway, queue pattern, tight density"
- "12:22 PM: 6 people clustered near entrance, bidirectional bottleneck"
- "2:45 PM: 3 people clustered near checkout counter, stationary cluster"

From these observations, you identify:
1. CHOKEPOINTS — physical features that consistently force close proximity
2. TEMPORAL PATTERNS — when crowding happens (time of day, day of week)
3. ROOT CAUSES — why the space design forces crowding (single entry/exit, counter placement, narrow corridors)
4. SPECIFIC RECOMMENDATIONS — concrete, actionable space redesign suggestions

Your report must be:
- Professional and actionable (a business owner should read this and know exactly what to change)
- Specific (not "consider redesigning" but "move the checkout counter 2 meters east to separate flowing and stationary traffic")
- Honest about limitations (you're working from spatial metadata, not architectural blueprints)
- Free of any identity or personal information

Format your response as a structured Markdown report with sections: Executive Summary, Identified Chokepoints, Temporal Patterns, Recommendations (prioritized by impact), and Methodology Note (explaining the privacy-preserving data collection).
"""


async def generate_space_report(observations: list[dict]) -> str:
    if not observations:
        return "_No observations yet — let the system collect at least 3 threshold events first._"

    obs_text = "\n".join([
        f"- {o.get('timestamp', '?')}: {o.get('spatial_issue', 'N/A')} | "
        f"Total: {o.get('total_people_visible', '?')} | "
        f"Chokepoints: {o.get('chokepoints', [])} | "
        f"Density: {o.get('overall_density', '?')} | "
        f"Clusters: {json.dumps(o.get('clusters', []))}"
        for o in observations
    ])

    user_msg = (
        f"Generate a Space Design Report from these "
        f"{len(observations)} spatial observations:\n\n{obs_text}"
    )
    text, model = await ai_provider.text_complete(
        system=REPORT_SYSTEM_PROMPT, user=user_msg, max_tokens=2000,
    )

    if model == "stub":
        # Fall back to a structured stub so the report still demonstrates value
        chokes: list[str] = []
        for o in observations:
            chokes.extend(o.get("chokepoints", []) or [])
        top = max(set(chokes), key=chokes.count) if chokes else "none observed"
        counts = [o.get("total_people_visible", 0) for o in observations]
        return f"""# Space Design Report (stub — no API key set)

## Executive Summary

Across **{len(observations)}** spatial observations, the system saw an
average of **{sum(counts) / max(len(counts), 1):.1f} people** per snapshot.
The most-observed chokepoint was: **{top}**.

## Identified Chokepoints

{chr(10).join(f"- {c}" for c in sorted(set(chokes))) if chokes else "- (none flagged this run)"}

## Temporal Patterns

This stub does not analyze time-of-day patterns. Add `OPENAI_API_KEY` or
`ANTHROPIC_API_KEY` to your `.env` for the full AI-generated report.

## Recommendations

1. (stub) Add an API key to enable full reasoning.
2. (stub) Calibrate CSI variance thresholds to your specific room.

## Methodology Note

Echolocate captures **spatial metadata only**. The camera frame is held in
RAM for ~2 seconds, analyzed for spatial patterns, then explicitly deleted.
This report is generated from {len(observations)} such metadata records —
no images were stored.
"""
    return text
