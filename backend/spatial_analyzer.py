"""
Vision-based spatial analysis.

The single image (base64 JPEG) is sent to whichever AI provider is configured
(Anthropic Claude preferred → OpenAI fallback → deterministic stub). We
receive back structured spatial metadata — counts, clusters, chokepoints,
density — but never any identifying detail about people. The image is *not*
persisted by this module; the caller deletes the base64 string after the
call returns.
"""

from __future__ import annotations

from typing import Optional

from . import ai_provider

SPATIAL_ANALYSIS_PROMPT = """You are analyzing a snapshot from a public space monitoring system during a pandemic. Your job is to describe the SPATIAL DISTRIBUTION of people — not identify anyone.

Analyze this image and return ONLY a JSON object with:
{
    "total_people_visible": <integer>,
    "clusters": [
        {
            "region": "<description of where in the frame: left/center/right + front/middle/back>",
            "near_feature": "<what physical feature they're near: door, counter, shelf, wall, hallway, table, etc. or 'open_area' if none>",
            "count": <number of people in this cluster>,
            "density": "<tight/moderate/spread>",
            "pattern": "<queue/stationary_cluster/passing_through/seated>"
        }
    ],
    "chokepoints": ["<list of physical features causing forced close proximity>"],
    "overall_density": "<sparse/moderate/crowded/packed>",
    "spatial_issue": "<one sentence describing the main spatial problem, or 'none' if well-distributed>"
}

CRITICAL RULES:
- Do NOT describe people's appearance, clothing, race, gender, or any identifying features
- Do NOT attempt to identify anyone
- Focus ONLY on spatial positioning relative to physical features of the space
- If you cannot clearly see people, say so honestly
- Return ONLY valid JSON, no other text
"""


async def analyze_snapshot(image_b64: str) -> Optional[dict]:
    """Returns the parsed JSON dict (with `_model` field added so the AI
    Decision Log can surface which provider answered) or None on failure."""
    parsed, model = await ai_provider.vision_analyze(
        image_b64=image_b64, prompt=SPATIAL_ANALYSIS_PROMPT,
    )
    if parsed is not None:
        parsed.setdefault("_model", model)
    return parsed
