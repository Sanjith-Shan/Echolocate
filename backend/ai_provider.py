"""
Vendor-agnostic AI helpers.

Selection rule, in priority order:
    1. ANTHROPIC_API_KEY → Claude (matches the original spec / Track sponsor)
    2. OPENAI_API_KEY    → GPT (fallback)
    3. neither           → stub responses

Two functions, both async:
    text_complete(system, user, max_tokens) -> (text, model_used)
    vision_analyze(image_b64, prompt, max_tokens) -> (parsed_json, model_used)

Returning the model_used lets the AI Decision Log surface which provider
actually answered, so the operator's audit trail stays honest.
"""

from __future__ import annotations

import json
import os
from typing import Optional


def active_provider() -> str:
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return "stub"


def active_model() -> str:
    p = active_provider()
    if p == "anthropic":
        return os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")
    if p == "openai":
        return os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    return "stub"


def _strip_fences(t: str) -> str:
    return t.replace("```json", "").replace("```", "").strip()


# ---------- Text completions ----------

async def text_complete(*, system: str, user: str, max_tokens: int = 500) -> tuple[str, str]:
    """Returns (text, model_used)."""
    p = active_provider()
    model = active_model()

    if p == "anthropic":
        try:
            import anthropic
            client = anthropic.Anthropic()
            resp = client.messages.create(
                model=model, max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return resp.content[0].text, model
        except Exception as e:
            return f"(Anthropic error: {e})", model

    if p == "openai":
        try:
            from openai import OpenAI
            client = OpenAI()
            resp = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            return resp.choices[0].message.content or "", model
        except Exception as e:
            return f"(OpenAI error: {e})", model

    return f"(Stub — set ANTHROPIC_API_KEY or OPENAI_API_KEY)\n{user}", "stub"


# ---------- Vision (image + prompt → structured JSON) ----------

async def vision_analyze(*, image_b64: str, prompt: str,
                         max_tokens: int = 600) -> tuple[Optional[dict], str]:
    """Returns (parsed_json_or_None, model_used)."""
    p = active_provider()
    model = active_model()

    if p == "anthropic":
        try:
            import anthropic
            client = anthropic.Anthropic()
            resp = client.messages.create(
                model=model, max_tokens=max_tokens,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64", "media_type": "image/jpeg",
                            "data": image_b64,
                        }},
                        {"type": "text", "text": prompt},
                    ],
                }],
            )
            text = _strip_fences(resp.content[0].text)
            return json.loads(text), model
        except Exception as e:
            print(f"[ai_provider] anthropic vision failed: {e}")
            return None, model

    if p == "openai":
        try:
            from openai import OpenAI
            client = OpenAI()
            resp = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}",
                        }},
                    ],
                }],
            )
            text = _strip_fences(resp.choices[0].message.content or "")
            return json.loads(text), model
        except Exception as e:
            print(f"[ai_provider] openai vision failed: {e}")
            return None, model

    # Stub: keyed off image length so different snapshots → different output
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
        "spatial_issue": "Stub analysis (no API key set)",
        "_stub": True,
    }, "stub"
