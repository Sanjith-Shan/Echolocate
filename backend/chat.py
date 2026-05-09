"""Conversational endpoint backed by Claude. Stub when no API key set."""

from __future__ import annotations

import json
import os
from typing import Optional

CHAT_SYSTEM_PROMPT = """You are Echolocate, a privacy-first public space monitoring AI. You help building operators and individuals understand space usage patterns and crowding risks.

You have access to current sensor data (WiFi CSI occupancy levels) and accumulated spatial observations. You can answer questions like:
- "How crowded is the space right now?"
- "When is the worst time for crowding?"
- "What's causing the bottleneck near the entrance?"
- "Is it safe to come in now?"

Important rules:
- You have NO camera feed. You cannot see the space right now.
- You can report CSI-based occupancy estimates (empty/low/moderate/high)
- You can reference spatial observations from past threshold events
- You NEVER have identity information about anyone
- Be honest about your limitations and confidence levels
- You are NOT a medical authority. Don't give medical advice about COVID risk levels.
"""


def _stub_reply(message: str, occupancy: dict, observations: list[dict]) -> str:
    level = occupancy.get("level", "unknown")
    n = len(observations)
    return (
        f"(Stub reply — set ANTHROPIC_API_KEY for the real model.)\n\n"
        f"Right now: occupancy is **{level}**. "
        f"I have {n} accumulated spatial observation{'s' if n != 1 else ''}. "
        f"You asked: \"{message[:200]}\""
    )


async def chat(message: str, occupancy: dict, recent_observations: list[dict]) -> str:
    if not os.getenv("ANTHROPIC_API_KEY"):
        return _stub_reply(message, occupancy, recent_observations)

    try:
        import anthropic
        client = anthropic.Anthropic()
        context = (
            f"Current occupancy: {json.dumps(occupancy)}\n"
            f"Recent observations ({len(recent_observations)} total):\n"
            f"{json.dumps(recent_observations[-5:], indent=2)}"
        )
        response = client.messages.create(
            model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5"),
            max_tokens=400,
            system=CHAT_SYSTEM_PROMPT + f"\n\nCURRENT DATA:\n{context}",
            messages=[{"role": "user", "content": message}],
        )
        return response.content[0].text
    except Exception as e:
        return f"(Chat backend error: {e})"
