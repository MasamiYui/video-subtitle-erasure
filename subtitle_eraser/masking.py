from __future__ import annotations

import cv2
import numpy as np

from subtitle_eraser.models import SubtitleEvent
from subtitle_eraser.regions import NormalizedRegion, boxes_intersect, region_to_box


def clamp_box(
    box: tuple[int, int, int, int],
    frame_shape: tuple[int, int, int],
    pad_x: int = 0,
    pad_y: int = 0,
) -> tuple[int, int, int, int]:
    height, width = frame_shape[:2]
    x1, y1, x2, y2 = box
    x1 = max(0, x1 - pad_x)
    y1 = max(0, y1 - pad_y)
    x2 = min(width, x2 + pad_x)
    y2 = min(height, y2 + pad_y)
    return x1, y1, max(x1 + 1, x2), max(y1 + 1, y2)


def mask_bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.where(mask > 0)
    if xs.size == 0 or ys.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def feather_mask(mask: np.ndarray, blur_size: int) -> np.ndarray:
    if blur_size <= 1:
        return (mask > 0).astype(np.float32)
    kernel = blur_size if blur_size % 2 == 1 else blur_size + 1
    blurred = cv2.GaussianBlur(mask.astype(np.float32) / 255.0, (kernel, kernel), 0)
    return np.clip(blurred, 0.0, 1.0)


def extract_text_mask(roi: np.ndarray) -> np.ndarray:
    # Hard subtitles are usually bright glyphs with thin dark outlines. Start from bright
    # cores, then link nearby edges so outline pixels are also included in the mask.
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    p90 = float(np.percentile(gray, 90))
    bright_threshold = int(min(245, max(150, round(p90))))

    white_core = gray >= bright_threshold
    edges = cv2.Canny(gray, 40, 120) > 0
    linked_edges = edges & cv2.dilate(white_core.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1).astype(bool)
    mask = white_core | linked_edges

    if mask.mean() < 0.001:
        # Some subtitle styles are dim or anti-aliased enough that the bright-core heuristic
        # misses them entirely, so fall back to a local adaptive threshold.
        adaptive = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            -5,
        )
        mask = adaptive > 0

    mask_u8 = (mask.astype(np.uint8)) * 255
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask_u8, connectivity=8)
    cleaned = np.zeros_like(mask_u8)
    for label in range(1, num_labels):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area >= 6:
            cleaned[labels == label] = 255

    cleaned = cv2.dilate(cleaned, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 3)), iterations=1)
    return cleaned


def _merge_full_region_mask(
    frame: np.ndarray,
    mask: np.ndarray,
    regions: list[NormalizedRegion],
    events: list[SubtitleEvent],
    pad_x: int,
    pad_y: int,
) -> None:
    height, width = frame.shape[:2]
    for region in regions:
        region_box = region_to_box(region, width, height)
        related_events = [event for event in events if boxes_intersect(event.box, region_box)]

        if related_events:
            # When OCR found subtitle boxes inside a manual ROI, tighten the search area around
            # their union instead of processing the full band every frame.
            union_box = (
                min(event.box[0] for event in related_events),
                min(event.box[1] for event in related_events),
                max(event.box[2] for event in related_events),
                max(event.box[3] for event in related_events),
            )
            x1, y1, x2, y2 = clamp_box(union_box, frame.shape, pad_x=pad_x * 2, pad_y=pad_y * 2)
        else:
            x1, y1, x2, y2 = clamp_box(region_box, frame.shape, pad_x=pad_x, pad_y=pad_y)

        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            continue
        local_mask = extract_text_mask(roi)
        coverage = float(local_mask.mean()) / 255.0
        if coverage < 0.001:
            continue
        if coverage > 0.16 and related_events:
            # Very high coverage usually means the ROI includes too much non-text content.
            # In that case keep the OCR-driven mask instead of flooding the whole region.
            continue
        local_mask = cv2.dilate(
            local_mask,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 3)),
            iterations=1,
        )
        mask[y1:y2, x1:x2] = np.maximum(mask[y1:y2, x1:x2], local_mask)


def build_frame_mask(
    frame: np.ndarray,
    events: list[SubtitleEvent],
    pad_x: int,
    pad_y: int,
    requested_regions: list[NormalizedRegion] | None = None,
    mode: str = "auto",
) -> np.ndarray:
    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    if mode == "manual-fixed" and requested_regions:
        # In fully manual mode the selected bands are always treated as candidate subtitle
        # regions, even if OCR temporarily misses a line.
        _merge_full_region_mask(frame, mask, requested_regions, events, pad_x=pad_x, pad_y=pad_y)

    for event in events:
        event_box = clamp_box(event.box, frame.shape, pad_x=pad_x, pad_y=pad_y)
        region_box = event_box
        geometry_mask = None

        if event.polygon and len(event.polygon) >= 3:
            # A polygon from OCR is usually tighter than the axis-aligned box, so crop the
            # local text mask to that geometry when it is available.
            full_poly_mask = np.zeros(frame.shape[:2], dtype=np.uint8)
            poly = np.array(event.polygon, dtype=np.int32)
            cv2.fillPoly(full_poly_mask, [poly], 255)
            polygon_box = clamp_box(mask_bbox(full_poly_mask) or event.box, frame.shape, pad_x=pad_x, pad_y=pad_y)
            px1, py1, px2, py2 = polygon_box
            cropped_poly_mask = full_poly_mask[py1:py2, px1:px2]
            if cropped_poly_mask.size > 0:
                region_box = polygon_box
                geometry_mask = cropped_poly_mask

        x1, y1, x2, y2 = region_box
        roi = frame[y1:y2, x1:x2]
        if roi.size == 0:
            continue

        local_mask = extract_text_mask(roi)
        coverage = float(local_mask.mean()) / 255.0
        if geometry_mask is not None:
            local_mask = cv2.bitwise_and(local_mask, geometry_mask)
            if coverage < 0.001 or coverage > 0.65:
                # If the heuristic mask is either empty or unrealistically dense, trust the
                # OCR polygon instead of the pixel-based extraction.
                local_mask = geometry_mask
        elif coverage < 0.001 or coverage > 0.65:
            # With only a bounding box available, falling back to the full box is safer than
            # leaving behind subtitle fragments.
            local_mask[:] = 255

        mask[y1:y2, x1:x2] = np.maximum(mask[y1:y2, x1:x2], local_mask)

    if mode == "semi-auto" and requested_regions:
        # Semi-auto keeps OCR as the primary signal, then uses the manual regions as a
        # conservative backstop to catch weak detections.
        _merge_full_region_mask(
            frame,
            mask,
            requested_regions,
            events,
            pad_x=max(4, pad_x // 2),
            pad_y=max(4, pad_y // 2),
        )

    mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 5)), iterations=1)
    return mask


def stabilize_temporal_masks(
    masks: list[np.ndarray],
    active_indices: list[int],
    radius: int = 1,
) -> list[np.ndarray]:
    if radius <= 0 or not masks or not active_indices:
        return masks

    active_set = set(active_indices)
    stabilized = [mask.copy() for mask in masks]
    link_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (max(3, radius * 4 + 3), max(3, radius * 2 + 3)),
    )
    close_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (max(5, radius * 4 + 5), max(3, radius * 2 + 3)),
    )

    for idx in active_indices:
        # Propagate nearby masks into the current frame to reduce subtitle edge flicker when
        # OCR timing is slightly early/late or the glyph strokes change across frames.
        merged = masks[idx].copy()
        for delta in range(1, radius + 1):
            for neighbor_idx in (idx - delta, idx + delta):
                if neighbor_idx not in active_set:
                    continue
                propagated = cv2.dilate(masks[neighbor_idx], link_kernel, iterations=1)
                merged = np.maximum(merged, propagated)
        merged = cv2.morphologyEx(merged, cv2.MORPH_CLOSE, close_kernel)
        stabilized[idx] = merged
    return stabilized
