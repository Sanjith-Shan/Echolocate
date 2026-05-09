"""Web Push (VAPID) notifier. Gracefully no-ops when not configured."""

from __future__ import annotations

import json
import os
import time
from typing import Optional


class PushNotifier:
    def __init__(self) -> None:
        self.vapid_private_key: Optional[str] = os.getenv("VAPID_PRIVATE_KEY")
        email = os.getenv("VAPID_CLAIMS_EMAIL", "mailto:dev@example.com")
        self.vapid_claims = {"sub": email}
        self.sent_count = 0
        self.failed_count = 0

    def configured(self) -> bool:
        return bool(self.vapid_private_key)

    def send(self, subscription_info: dict, title: str, body: str, data: Optional[dict] = None) -> bool:
        if not self.configured() or not subscription_info:
            self.failed_count += 1
            return False

        try:
            from pywebpush import webpush, WebPushException  # lazy
        except ImportError:
            print("[push] pywebpush not installed")
            self.failed_count += 1
            return False

        payload = json.dumps({
            "title": title,
            "body": body,
            "icon": "/icon-192.png",
            "badge": "/badge-72.png",
            "data": data or {},
            "tag": f"echolocate-{int(time.time())}",
        })

        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=self.vapid_private_key,
                vapid_claims=dict(self.vapid_claims),  # webpush mutates this
            )
            self.sent_count += 1
            return True
        except Exception as e:
            print(f"[push] send failed: {e}")
            self.failed_count += 1
            return False
