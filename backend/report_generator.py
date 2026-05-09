"""Claude Space Design Report generation from accumulated spatial metadata."""

from __future__ import annotations

import json
import os
from typing import Iterable

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


def _stub_report(observations: list[dict]) -> str:
    counts = [o.get("total_people_visible", 0) for o in observations]
    chokes: list[str] = []
    for o in observations:
        chokes.extend(o.get("chokepoints", []) or [])
    top_choke = max(set(chokes), key=chokes.count) if chokes else "none observed"
    return f"""# Space Design Report (stub — no ANTHROPIC_API_KEY)

## Executive Summary

Across **{len(observations)}** spatial observations, the system saw an
average of **{sum(counts) / max(len(counts), 1):.1f} people** per snapshot.
The most-observed chokepoint was: **{top_choke}**.

## Identified Chokepoints

{chr(10).join(f"- {c}" for c in sorted(set(chokes))) if chokes else "- (none flagged this run)"}

## Temporal Patterns

This stub does not analyze time-of-day patterns. Set `ANTHROPIC_API_KEY`
for the full Claude-generated report.

## Recommendations

1. (stub) Add `ANTHROPIC_API_KEY` to enable full reasoning.
2. (stub) Calibrate CSI variance thresholds to your specific room.

## Methodology Note

Echolocate captures **spatial metadata only**. The camera frame is held in
RAM for ~2 seconds, analyzed for spatial patterns, then explicitly deleted.
This report is generated from {len(observations)} such metadata records —
no images were stored.
"""


async def generate_space_report(observations: list[dict]) -> str:
    if not observations:
        return "_No observations yet — let the system collect at least 3 threshold events first._"

    if not os.getenv("ANTHROPIC_API_KEY"):
        return _stub_report(observations)

    try:
        import anthropic
        client = anthropic.Anthropic()

        obs_text = "\n".join([
            f"- {o.get('timestamp', '?')}: {o.get('spatial_issue', 'N/A')} | "
            f"Total: {o.get('total_people_visible', '?')} | "
            f"Chokepoints: {o.get('chokepoints', [])} | "
            f"Density: {o.get('overall_density', '?')} | "
            f"Clusters: {json.dumps(o.get('clusters', []))}"
            for o in observations
        ])

        response = client.messages.create(
            model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5"),
            max_tokens=2000,
            system=REPORT_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Generate a Space Design Report from these "
                    f"{len(observations)} spatial observations:\n\n{obs_text}"
                ),
            }],
        )
        return response.content[0].text
    except Exception as e:
        return f"_Report generation failed: {e}_\n\nFalling back to stub:\n\n{_stub_report(observations)}"
