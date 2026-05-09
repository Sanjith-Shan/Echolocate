"""
Ephemeral webcam capture.

The Logitech Brio 101 (or any V4L/AVFoundation device) is opened ONLY when
this function is called, the frame is encoded as JPEG-base64 in RAM, then
the cv2 capture handle is released. The caller is responsible for `del`-ing
the returned base64 string after the Vision call returns.

If OpenCV isn't available or no camera is present, returns None — the rest
of the pipeline gracefully degrades (operator dashboard still works, just no
spatial reports).
"""

from __future__ import annotations

import base64
from typing import Optional

try:
    import cv2  # type: ignore
    HAVE_CV2 = True
except ImportError:
    HAVE_CV2 = False


class EphemeralCamera:
    def __init__(self, device_index: int = 1, max_width: int = 640, jpeg_quality: int = 70):
        self.device_index = device_index
        self.max_width = max_width
        self.jpeg_quality = jpeg_quality

    def available(self) -> bool:
        return HAVE_CV2

    def capture_and_encode(self) -> Optional[str]:
        """Capture one frame, return base64 JPEG. Frame buffer is deleted before return."""
        if not HAVE_CV2:
            return None

        cap = cv2.VideoCapture(self.device_index)
        if not cap.isOpened():
            cap.release()
            return None

        try:
            ret, frame = cap.read()
        finally:
            cap.release()

        if not ret or frame is None:
            return None

        h, w = frame.shape[:2]
        if w > self.max_width:
            scale = self.max_width / w
            frame = cv2.resize(frame, (self.max_width, int(h * scale)))

        ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        if not ok:
            return None

        b64 = base64.b64encode(buffer).decode("utf-8")

        # Privacy invariant: the raw frame must not survive this function.
        del frame
        del buffer
        return b64
