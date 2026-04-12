from __future__ import annotations

import cv2
import numpy as np

from subtitle_eraser.masking import build_frame_mask
from subtitle_eraser.models import SubtitleEvent


def test_manual_fixed_region_builds_mask_without_events() -> None:
    frame = np.zeros((240, 480, 3), dtype=np.uint8)
    cv2.putText(frame, "TEST", (120, 210), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3, cv2.LINE_AA)

    mask = build_frame_mask(
        frame,
        events=[],
        pad_x=8,
        pad_y=6,
        requested_regions=[(0.18, 0.68, 0.82, 0.96)],
        mode="manual-fixed",
    )

    assert int(mask.sum()) > 0
    assert int(mask[180:225, 100:300].sum()) > 0


def test_event_box_builds_mask_in_auto_mode() -> None:
    frame = np.zeros((240, 480, 3), dtype=np.uint8)
    cv2.putText(frame, "HELLO", (80, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)

    event = SubtitleEvent(
        index=0,
        start_time=0.0,
        end_time=1.0,
        start_frame=0,
        end_frame=25,
        text="HELLO",
        confidence=0.98,
        box=(70, 160, 290, 220),
        polygon=None,
    )
    mask = build_frame_mask(frame, [event], pad_x=10, pad_y=8)

    assert int(mask.sum()) > 0
    assert int(mask[160:220, 70:300].sum()) > 0
