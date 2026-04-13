from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

from subtitle_eraser.detection import detect_subtitles
from subtitle_eraser.masking import build_frame_mask
from subtitle_eraser.models import DetectionResult, SubtitleEvent, VideoInfo


@dataclass(slots=True)
class EvaluationMetrics:
    score: float
    residual_ratio: float
    spill_score: float
    reference_mass: float
    residual_mass: float
    reference_events: int
    residual_events: int
    sampled_frames: int

    def to_dict(self) -> dict:
        return asdict(self)


def weighted_event_mass(events: list[SubtitleEvent], video_info: VideoInfo) -> float:
    if video_info.width <= 0 or video_info.height <= 0:
        return 0.0
    frame_area = float(video_info.width * video_info.height)
    total = 0.0
    for event in events:
        x1, y1, x2, y2 = event.box
        duration_frames = max(1, event.end_frame - event.start_frame + 1)
        area_ratio = max(1.0 / frame_area, ((x2 - x1) * (y2 - y1)) / frame_area)
        text_weight = max(1.0, min(8.0, len(event.text or "")))
        total += float(event.confidence) * duration_frames * area_ratio * text_weight
    return total


def select_sample_clips(
    video_info: VideoInfo,
    events: list[SubtitleEvent],
    clip_duration: float = 6.0,
    max_clips: int = 3,
) -> list[tuple[float, float]]:
    if clip_duration <= 0:
        raise ValueError("clip_duration must be positive")

    if video_info.duration <= clip_duration or not events:
        return [(0.0, min(video_info.duration, clip_duration))]

    chosen: list[tuple[float, float]] = []
    event_indices = [0, len(events) // 2, len(events) - 1]
    weighted_idx = max(
        range(len(events)),
        key=lambda idx: weighted_event_mass([events[idx]], video_info),
    )
    if weighted_idx not in event_indices:
        event_indices.append(weighted_idx)

    for idx in event_indices:
        event = events[idx]
        center = (event.start_time + event.end_time) / 2.0
        start = max(0.0, min(center - clip_duration / 2.0, max(0.0, video_info.duration - clip_duration)))
        end = min(video_info.duration, start + clip_duration)
        if any(min(end, existing_end) - max(start, existing_start) >= clip_duration * 0.5 for existing_start, existing_end in chosen):
            continue
        chosen.append((round(start, 3), round(end, 3)))
        if len(chosen) >= max_clips:
            break

    if not chosen:
        chosen.append((0.0, min(video_info.duration, clip_duration)))
    return chosen


def _intervals_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    return max(start_a, start_b) <= min(end_a, end_b)


def _boxes_overlap(box_a: tuple[int, int, int, int], box_b: tuple[int, int, int, int], margin: int = 0) -> bool:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    return not (
        ax2 + margin < bx1
        or bx2 + margin < ax1
        or ay2 + margin < by1
        or by2 + margin < ay1
    )


def filter_residual_events(
    reference_events: list[SubtitleEvent],
    output_events: list[SubtitleEvent],
    frame_margin: int = 4,
    box_margin: int = 28,
) -> list[SubtitleEvent]:
    filtered: list[SubtitleEvent] = []
    for event in output_events:
        if any(
            _intervals_overlap(
                event.start_frame,
                event.end_frame,
                ref.start_frame - frame_margin,
                ref.end_frame + frame_margin,
            )
            and _boxes_overlap(event.box, ref.box, margin=box_margin)
            for ref in reference_events
        ):
            filtered.append(event)
    return filtered


def _sample_frame_indices(events: list[SubtitleEvent], max_frames: int = 10) -> list[int]:
    active_frames: list[int] = []
    for event in events:
        active_frames.extend([event.start_frame, (event.start_frame + event.end_frame) // 2, event.end_frame])
    unique = sorted(set(active_frames))
    if len(unique) <= max_frames:
        return unique
    step = (len(unique) - 1) / float(max_frames - 1)
    return [unique[min(len(unique) - 1, round(index * step))] for index in range(max_frames)]


def _evaluation_band_box(reference_detection: DetectionResult, margin_x: int = 36, margin_y: int = 28) -> tuple[int, int, int, int]:
    width = reference_detection.video_info.width
    height = reference_detection.video_info.height
    if reference_detection.requested_regions:
        xs1 = [int(region[0] * width) for region in reference_detection.requested_regions]
        ys1 = [int(region[1] * height) for region in reference_detection.requested_regions]
        xs2 = [int(region[2] * width) for region in reference_detection.requested_regions]
        ys2 = [int(region[3] * height) for region in reference_detection.requested_regions]
        return (
            max(0, min(xs1) - margin_x),
            max(0, min(ys1) - margin_y),
            min(width, max(xs2) + margin_x),
            min(height, max(ys2) + margin_y),
        )
    if reference_detection.events:
        xs1 = [event.box[0] for event in reference_detection.events]
        ys1 = [event.box[1] for event in reference_detection.events]
        xs2 = [event.box[2] for event in reference_detection.events]
        ys2 = [event.box[3] for event in reference_detection.events]
        return (
            max(0, min(xs1) - margin_x),
            max(0, min(ys1) - margin_y),
            min(width, max(xs2) + margin_x),
            min(height, max(ys2) + margin_y),
        )
    return (0, int(height * 0.55), width, height)


def compute_spill_score(
    input_path: str,
    output_path: str,
    reference_detection: DetectionResult,
    pad_x: int,
    pad_y: int,
    max_frames: int = 10,
) -> tuple[float, int]:
    sample_frames = _sample_frame_indices(reference_detection.events, max_frames=max_frames)
    if not sample_frames:
        return 0.0, 0

    input_cap = cv2.VideoCapture(input_path)
    output_cap = cv2.VideoCapture(output_path)
    if not input_cap.isOpened() or not output_cap.isOpened():
        raise ValueError("Cannot open videos for evaluation")

    band_x1, band_y1, band_x2, band_y2 = _evaluation_band_box(reference_detection)
    total_spill = 0.0
    counted = 0

    try:
        for frame_idx in sample_frames:
            input_cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            output_cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ok_in, input_frame = input_cap.read()
            ok_out, output_frame = output_cap.read()
            if not ok_in or not ok_out:
                continue

            frame_events = [
                event
                for event in reference_detection.events
                if event.start_frame <= frame_idx <= event.end_frame
            ]
            if not frame_events:
                continue

            mask = build_frame_mask(
                input_frame,
                frame_events,
                pad_x=pad_x,
                pad_y=pad_y,
                requested_regions=reference_detection.requested_regions,
                mode=reference_detection.mode,
            )
            diff = cv2.absdiff(input_frame, output_frame)
            diff_gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0

            mask_band = mask[band_y1:band_y2, band_x1:band_x2]
            diff_band = diff_gray[band_y1:band_y2, band_x1:band_x2]
            outside = diff_band[mask_band == 0]
            if outside.size == 0:
                continue

            total_spill += float(outside.mean())
            counted += 1
    finally:
        input_cap.release()
        output_cap.release()

    return (total_spill / counted if counted else 0.0), counted


def evaluate_processed_video(
    input_path: str,
    output_path: str,
    reference_detection: DetectionResult,
    *,
    subtitle_ocr_project: str | None,
    language: str,
    position_mode: str,
    roi_bottom_ratio: float,
    mode: str,
    requested_regions: list[tuple[float, float, float, float]] | None,
    sample_interval: float,
    pad_x: int,
    pad_y: int,
    ocr_det_db_thresh: float | None = None,
    ocr_det_db_box_thresh: float | None = None,
    prefilter_enabled: bool | None = False,
) -> EvaluationMetrics:
    residual_detection = detect_subtitles(
        video_path=output_path,
        subtitle_ocr_project=subtitle_ocr_project,
        sample_interval=min(sample_interval, 0.12),
        language=language,
        position_mode=position_mode,
        roi_bottom_ratio=roi_bottom_ratio,
        requested_regions=requested_regions,
        mode=mode,
        event_lead_frames=0,
        event_trail_frames=0,
        merge_threshold=0.64,
        ocr_det_db_thresh=ocr_det_db_thresh,
        ocr_det_db_box_thresh=ocr_det_db_box_thresh,
        prefilter_enabled=prefilter_enabled,
    )
    filtered_residual_events = filter_residual_events(reference_detection.events, residual_detection.events)

    reference_mass = max(1e-6, weighted_event_mass(reference_detection.events, reference_detection.video_info))
    residual_mass = weighted_event_mass(filtered_residual_events, residual_detection.video_info)
    residual_ratio = min(2.0, residual_mass / reference_mass)

    spill_score, sampled_frames = compute_spill_score(
        input_path=input_path,
        output_path=output_path,
        reference_detection=reference_detection,
        pad_x=pad_x,
        pad_y=pad_y,
    )
    score = residual_ratio * 0.88 + min(1.0, spill_score * 10.0) * 0.12

    return EvaluationMetrics(
        score=score,
        residual_ratio=residual_ratio,
        spill_score=spill_score,
        reference_mass=reference_mass,
        residual_mass=residual_mass,
        reference_events=len(reference_detection.events),
        residual_events=len(filtered_residual_events),
        sampled_frames=sampled_frames,
    )


def write_evaluation_metrics(metrics: EvaluationMetrics, output_path: str | Path) -> None:
    Path(output_path).write_text(json.dumps(metrics.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
