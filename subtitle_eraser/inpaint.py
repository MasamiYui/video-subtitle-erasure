from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from subtitle_eraser.masking import extract_text_mask, mask_bbox


@dataclass(slots=True)
class InpaintConfig:
    backend: str = "telea"
    spatial_radius: int = 3
    context_margin: int = 80
    cleanup_passes: int = 1
    cleanup_guard_margin: int = 10
    cleanup_max_coverage: float = 0.045
    max_temporal_references: int = 6
    flow_scale: float = 0.5
    temporal_min_consensus: int = 2
    temporal_max_std: float = 14.0


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
            filled_roi = frame_roi.copy()
            temporal_valid = np.zeros(mask_roi.shape, dtype=bool)
            if self.config.backend == "flow-guided":
                temporal_fill, temporal_valid = self._temporal_fill_roi(frames, masks, target_idx, roi)
                if temporal_fill is not None and temporal_valid.any():
                    filled_roi[temporal_valid] = temporal_fill[temporal_valid]

            remaining_mask = mask_roi.copy()
            if temporal_valid.any():
                remaining_mask[temporal_valid] = 0
            if np.any(remaining_mask > 0):
                fill = cv2.inpaint(
                    filled_roi,
                    remaining_mask,
                    self.config.spatial_radius,
                    cv2.INPAINT_TELEA,
                ).astype(np.float32)
                filled_roi[remaining_mask > 0] = np.clip(fill, 0, 255).astype(np.uint8)[remaining_mask > 0]

            output = frame.copy()
            output_roi = output[y1:y2, x1:x2]
            masked = mask_roi > 0
            output_roi[masked] = filled_roi[masked]
            outputs[target_idx] = self._cleanup_residual_text(output, mask)

        return outputs

    def _temporal_fill_roi(
        self,
        frames: list[np.ndarray],
        masks: list[np.ndarray],
        target_idx: int,
        roi: tuple[int, int, int, int],
    ) -> tuple[np.ndarray | None, np.ndarray]:
        x1, y1, x2, y2 = roi
        target_roi = frames[target_idx][y1:y2, x1:x2]
        target_mask = masks[target_idx][y1:y2, x1:x2]
        if target_roi.size == 0 or not np.any(target_mask > 0):
            return None, np.zeros(target_mask.shape, dtype=bool)

        target_gray = cv2.cvtColor(target_roi, cv2.COLOR_BGR2GRAY)
        accum = np.zeros(target_roi.shape, dtype=np.float32)
        accum_sq = np.zeros(target_roi.shape, dtype=np.float32)
        vote_count = np.zeros(target_mask.shape, dtype=np.float32)
        references = sorted(
            (idx for idx in range(len(frames)) if idx != target_idx),
            key=lambda idx: abs(idx - target_idx),
        )[: self.config.max_temporal_references]

        for ref_idx in references:
            ref_roi = frames[ref_idx][y1:y2, x1:x2]
            ref_mask = masks[ref_idx][y1:y2, x1:x2]
            if ref_roi.size == 0:
                continue
            if float((ref_mask > 0).mean()) >= 0.7:
                continue
            outside_mask = target_mask == 0
            if np.any(outside_mask):
                diff = cv2.absdiff(target_roi, ref_roi)
                if float(diff[outside_mask].mean()) < 6.0:
                    warped_ref, warped_ref_mask = ref_roi, ref_mask
                else:
                    warped_ref, warped_ref_mask = self._warp_reference_to_target(
                        target_gray=target_gray,
                        reference_roi=ref_roi,
                        reference_mask=ref_mask,
                    )
            else:
                warped_ref, warped_ref_mask = self._warp_reference_to_target(
                    target_gray=target_gray,
                    reference_roi=ref_roi,
                    reference_mask=ref_mask,
                )
            valid = (target_mask > 0) & (warped_ref_mask == 0)
            if not np.any(valid):
                continue
            warped_ref_f32 = warped_ref.astype(np.float32)
            accum[valid] += warped_ref_f32[valid]
            accum_sq[valid] += warped_ref_f32[valid] ** 2
            vote_count[valid] += 1.0

        min_consensus = max(1, int(self.config.temporal_min_consensus))
        if not np.any(vote_count >= float(min_consensus)):
            return None, np.zeros(target_mask.shape, dtype=bool)

        count_safe = np.maximum(vote_count[..., None], 1.0)
        mean = accum / count_safe
        variance = np.maximum(accum_sq / count_safe - mean**2, 0.0)
        temporal_std = np.sqrt(np.mean(variance, axis=2))
        valid = (vote_count >= float(min_consensus)) & (temporal_std <= float(self.config.temporal_max_std))
        if not np.any(valid):
            return None, valid

        output = target_roi.copy()
        output[valid] = np.clip(mean[valid], 0, 255).astype(np.uint8)
        return output, valid

    def _warp_reference_to_target(
        self,
        target_gray: np.ndarray,
        reference_roi: np.ndarray,
        reference_mask: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        ref_gray = cv2.cvtColor(reference_roi, cv2.COLOR_BGR2GRAY)
        height, width = target_gray.shape[:2]
        scale = min(1.0, max(0.2, float(self.config.flow_scale)))

        if scale < 0.999:
            small_size = (max(8, int(round(width * scale))), max(8, int(round(height * scale))))
            target_small = cv2.resize(target_gray, small_size, interpolation=cv2.INTER_AREA)
            ref_small = cv2.resize(ref_gray, small_size, interpolation=cv2.INTER_AREA)
        else:
            target_small = target_gray
            ref_small = ref_gray

        flow = cv2.calcOpticalFlowFarneback(
            target_small,
            ref_small,
            None,
            pyr_scale=0.5,
            levels=3,
            winsize=21,
            iterations=3,
            poly_n=5,
            poly_sigma=1.2,
            flags=0,
        )
        if scale < 0.999:
            flow = cv2.resize(flow, (width, height), interpolation=cv2.INTER_LINEAR)
            flow /= scale

        grid_x, grid_y = np.meshgrid(
            np.arange(width, dtype=np.float32),
            np.arange(height, dtype=np.float32),
        )
        map_x = grid_x + flow[..., 0]
        map_y = grid_y + flow[..., 1]
        warped_ref = cv2.remap(
            reference_roi,
            map_x,
            map_y,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT_101,
        )
        warped_mask = cv2.remap(
            reference_mask,
            map_x,
            map_y,
            interpolation=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=255,
        )
        return warped_ref, warped_mask

    def _cleanup_residual_text(self, frame: np.ndarray, allowed_mask: np.ndarray) -> np.ndarray:
        if self.config.cleanup_passes <= 0:
            return frame

        cleaned = frame.copy()
        guard_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (max(5, self.config.cleanup_guard_margin * 2 + 1), max(3, self.config.cleanup_guard_margin + 1)),
        )

        for _ in range(self.config.cleanup_passes):
            bbox = mask_bbox(allowed_mask)
            if bbox is None:
                break
            x1, y1, x2, y2 = self._expand_roi(bbox, cleaned.shape)
            frame_roi = cleaned[y1:y2, x1:x2]
            allowed_roi = allowed_mask[y1:y2, x1:x2]
            if frame_roi.size == 0:
                break

            residual_mask = extract_text_mask(frame_roi)
            guarded = cv2.dilate(allowed_roi, guard_kernel, iterations=1)
            residual_mask = cv2.bitwise_and(residual_mask, guarded)
            coverage = float(residual_mask.mean()) / 255.0
            if coverage < 0.002:
                break
            if coverage > self.config.cleanup_max_coverage:
                break

            cleanup_mask = cv2.dilate(
                residual_mask,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 3)),
                iterations=1,
            )
            fill = cv2.inpaint(
                frame_roi,
                cleanup_mask,
                max(1, self.config.spatial_radius + 1),
                cv2.INPAINT_TELEA,
            )
            write_mask = cleanup_mask > 0
            frame_roi[write_mask] = fill[write_mask]
            cleaned[y1:y2, x1:x2] = frame_roi
        return cleaned

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
