"""
Claude Vision spatial analysis.

The single image (base64 JPEG) is sent to Claude. We receive back structured
spatial metadata — counts, clusters, chokepoints, density — but never any
identifying detail about the people. The image is *not* persisted by this
module; the caller deletes the base64 string after the call returns.

If ANTHROPIC_API_KEY is not set, returns a deterministic stub so the rest of
the pipeline can be tested without burning API quota.
"""

from __future__ import annotations

import json
import os
from typing import Optional

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


def _stub_response(image_b64: str) -> dict:
    """Deterministic stand-in when no API key is configured. Keyed off the
    length of the base64 so different snapshots produce different stubs."""
    h = len(image_b64) % 4
    densities = ["sparse", "moderate", "crowded", "packed"]
    return {
        "total_people_visible": 3 + h,
        "clusters": [{
            "region": "center middle",
            "near_feature": "open_area",
            "count": 3 + h,
            "density": "moderate",
            "pattern": "stationary_cluster",
        }],
        "chokepoints": [],
        "overall_density": densities[h],
        "spatial_issue": "Stub analysis (no ANTHROPIC_API_KEY set)",
        "_stub": True,
    }


async def analyze_snapshot(image_b64: str) -> Optional[dict]:
    if not os.getenv("ANTHROPIC_API_KEY"):
        return _stub_response(image_b64)

    try:
        import anthropic  # lazy import
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5"),
            max_tokens=600,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/jpeg", "data": image_b64
                    }},
                    {"type": "text", "text": SPATIAL_ANALYSIS_PROMPT},
                ],
            }],
        )
        text = response.content[0].text
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        print(f"[spatial] Claude vision failed: {e}")
        return None
