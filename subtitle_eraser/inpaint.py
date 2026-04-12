from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from subtitle_eraser.masking import mask_bbox


@dataclass(slots=True)
class InpaintConfig:
    spatial_radius: int = 3
    context_margin: int = 80


class HybridTemporalInpainter:
    def __init__(self, config: InpaintConfig):
        self.config = config

    def process_segment(
        self,
        frames: list[np.ndarray],
        masks: list[np.ndarray],
        target_local_indices: list[int],
    ) -> dict[int, np.ndarray]:
        outputs: dict[int, np.ndarray] = {}

        for target_idx in target_local_indices:
            frame = frames[target_idx]
            mask = masks[target_idx]
            bbox = mask_bbox(mask)
            if bbox is None:
                outputs[target_idx] = frame
                continue

            roi = self._expand_roi(bbox, frame.shape)
            x1, y1, x2, y2 = roi
            frame_roi = frame[y1:y2, x1:x2]
            mask_roi = mask[y1:y2, x1:x2]
            fill = cv2.inpaint(
                frame_roi,
                mask_roi,
                self.config.spatial_radius,
                cv2.INPAINT_TELEA,
            ).astype(np.float32)

            output = frame.copy()
            output_roi = output[y1:y2, x1:x2]
            masked = mask_roi > 0
            output_roi[masked] = np.clip(fill, 0, 255).astype(np.uint8)[masked]
            outputs[target_idx] = output

        return outputs

    def _expand_roi(self, bbox: tuple[int, int, int, int], frame_shape: tuple[int, int, int]) -> tuple[int, int, int, int]:
        x1, y1, x2, y2 = bbox
        height, width = frame_shape[:2]
        margin = self.config.context_margin
        return (
            max(0, x1 - margin),
            max(0, y1 - margin),
            min(width, x2 + margin),
            min(height, y2 + margin),
        )
