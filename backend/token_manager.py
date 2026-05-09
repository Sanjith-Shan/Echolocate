"""
Anonymous rotating-token contact tracing.

Inspired by the Apple/Google Exposure Notifications framework. The persistent
`token_id` is generated server-side once and returned to the client; only
the client stores it. The server tracks per-token zone+time history so it
can compute zone-time overlaps when someone reports a positive test, but it
never knows *who* the token belongs to.

The rotating ID rotates every 15 minutes — even if a single MAC or IP could
ever be associated with a token, the rotating ID can't be linked across
windows without server cooperation.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional


class AnonymousTokenManager:
    ROTATION_SECONDS = 15 * 60
    OVERLAP_WINDOW_SECONDS = 30 * 60
    HISTORY_DAYS = 14

    def __init__(self) -> None:
        # token_id -> {push_subscription, zone_history, current_rotating_id, last_rotation}
        self.registered_tokens: dict[str, dict] = {}

    # ---------- Registration / rotation ----------

    def register(self, push_subscription: Optional[dict] = None) -> str:
        token_id = str(uuid.uuid4())
        self.registered_tokens[token_id] = {
            "push_subscription": push_subscription or {},
            "zone_history": [],
            "current_rotating_id": self._generate_rotating_id(token_id),
            "last_rotation": time.time(),
        }
        return token_id

    def _generate_rotating_id(self, token_id: str) -> str:
        time_window = int(time.time() // self.ROTATION_SECONDS)
        return hashlib.sha256(f"{token_id}:{time_window}".encode()).hexdigest()[:16]

    def _maybe_rotate(self, token_id: str) -> None:
        token = self.registered_tokens[token_id]
        if time.time() - token["last_rotation"] > self.ROTATION_SECONDS:
            token["current_rotating_id"] = self._generate_rotating_id(token_id)
            token["last_rotation"] = time.time()

    # ---------- Check-in ----------

    def checkin(self, token_id: str, zone: str) -> bool:
        if token_id not in self.registered_tokens:
            return False
        self._maybe_rotate(token_id)
        token = self.registered_tokens[token_id]
        token["zone_history"].append({
            "zone": zone,
            "time": datetime.now().isoformat(),
            "rotating_id": token["current_rotating_id"],
        })
        cutoff = (datetime.now() - timedelta(days=self.HISTORY_DAYS)).isoformat()
        token["zone_history"] = [h for h in token["zone_history"] if h["time"] > cutoff]
        return True

    # ---------- Exposure notifications ----------

    def find_overlaps(self, token_id: str) -> list[str]:
        """Return token_ids that overlapped with this token's zone history.
        The caller is responsible for sending notifications to them."""
        if token_id not in self.registered_tokens:
            return []

        reporter = self.registered_tokens[token_id]
        reporter_history = reporter["zone_history"]
        overlapping: list[str] = []

        for other_id, other_token in self.registered_tokens.items():
            if other_id == token_id:
                continue
            for r_entry in reporter_history:
                hit = False
                for o_entry in other_token["zone_history"]:
                    if r_entry["zone"] != o_entry["zone"]:
                        continue
                    r_time = datetime.fromisoformat(r_entry["time"])
                    o_time = datetime.fromisoformat(o_entry["time"])
                    if abs((r_time - o_time).total_seconds()) < self.OVERLAP_WINDOW_SECONDS:
                        overlapping.append(other_id)
                        hit = True
                        break
                if hit:
                    break

        return overlapping
