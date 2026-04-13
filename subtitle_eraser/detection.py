from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import cv2

from subtitle_eraser.bridge import get_subtitle_service_instance
from subtitle_eraser.models import DetectionResult, SubtitleEvent, VideoInfo
from subtitle_eraser.progress import ProgressCallback
from subtitle_eraser.regions import NormalizedRegion, any_region_intersects


def _probe_video(video_path: str) -> VideoInfo:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Cannot open video: {video_path}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    cap.release()

    duration = total_frames / fps if fps > 0 else 0.0
    return VideoInfo(
        fps=fps,
        total_frames=total_frames,
        width=width,
        height=height,
        duration=duration,
    )


def _box_from_polygon(polygon: list[tuple[float, float]] | None) -> tuple[int, int, int, int] | None:
    if not polygon:
        return None
    xs = [float(x) for x, _ in polygon]
    ys = [float(y) for _, y in polygon]
    return (
        int(round(min(xs))),
        int(round(min(ys))),
        int(round(max(xs))),
        int(round(max(ys))),
    )


def _normalize_polygon(polygon: list[tuple[float, float]] | None) -> list[tuple[int, int]] | None:
    if not polygon:
        return None
    return [(int(round(x)), int(round(y))) for x, y in polygon]


def filter_events_by_regions(
    events: list[SubtitleEvent],
    regions: list[NormalizedRegion] | None,
    width: int,
    height: int,
) -> list[SubtitleEvent]:
    if not regions:
        return events
    return [event for event in events if any_region_intersects(event.box, regions, width, height)]


def expand_event_windows(
    events: list[SubtitleEvent],
    total_frames: int,
    fps: float,
    lead_frames: int,
    trail_frames: int,
) -> list[SubtitleEvent]:
    if not events:
        return []

    expanded: list[SubtitleEvent] = []
    for event in events:
        start_frame = max(0, event.start_frame - max(0, lead_frames))
        end_frame = min(total_frames - 1, event.end_frame + max(0, trail_frames))
        expanded.append(
            replace(
                event,
                start_frame=start_frame,
                end_frame=max(start_frame, end_frame),
                start_time=start_frame / fps if fps > 0 else event.start_time,
                end_time=end_frame / fps if fps > 0 else event.end_time,
            )
        )
    return expanded


def _notify(progress_callback: ProgressCallback | None, stage: str, progress: int, message: str) -> None:
    if progress_callback is not None:
        progress_callback(stage, progress, message)


def detect_subtitles(
    video_path: str,
    subtitle_ocr_project: str | None = None,
    sample_interval: float = 0.25,
    language: str = "ch",
    position_mode: str = "bottom",
    roi_bottom_ratio: float = 0.34,
    requested_regions: list[NormalizedRegion] | None = None,
    mode: str = "auto",
    event_lead_frames: int = 2,
    event_trail_frames: int = 8,
    merge_threshold: float = 0.78,
    ocr_det_db_thresh: float | None = None,
    ocr_det_db_box_thresh: float | None = None,
    prefilter_enabled: bool | None = None,
    progress_callback: ProgressCallback | None = None,
) -> DetectionResult:
    _notify(progress_callback, "detecting", 5, "读取视频信息")
    video_info = _probe_video(video_path)
    service = get_subtitle_service_instance(subtitle_ocr_project)
    _notify(progress_callback, "detecting", 12, "运行字幕定位")
    result = service.extract_subtitles(
        video_path=video_path,
        language=language,
        sample_interval=sample_interval,
        detect_region=True,
        roi_bottom_ratio=roi_bottom_ratio,
        subtitle_position_mode=position_mode,
        subtitle_geometry_mode="axis_aligned",
        merge_threshold=merge_threshold,
        det_db_thresh=ocr_det_db_thresh,
        det_db_box_thresh=ocr_det_db_box_thresh,
        prefilter_enabled=prefilter_enabled,
    )

    events: list[SubtitleEvent] = []
    _notify(progress_callback, "detecting", 20, "整理字幕事件")
    for item in result.subtitles:
        polygon = _normalize_polygon(item.polygon)
        box = _box_from_polygon(item.polygon) or tuple(int(v) for v in item.box) if item.box else None
        if box is None:
            continue

        start_frame = max(0, int(item.start_time * video_info.fps))
        end_frame = min(video_info.total_frames - 1, int(item.end_time * video_info.fps) + 1)
        events.append(
            SubtitleEvent(
                index=int(item.index),
                start_time=float(item.start_time),
                end_time=float(item.end_time),
                start_frame=start_frame,
                end_frame=max(start_frame, end_frame),
                text=str(item.text),
                confidence=float(item.confidence),
                box=tuple(int(v) for v in box),
                polygon=polygon,
            )
        )

    events = filter_events_by_regions(events, requested_regions, video_info.width, video_info.height)
    events = expand_event_windows(
        events,
        total_frames=video_info.total_frames,
        fps=video_info.fps,
        lead_frames=event_lead_frames,
        trail_frames=event_trail_frames,
    )

    anchors = []
    for anchor in result.anchors:
        anchors.append(
            {
                "center_x": anchor.center_x,
                "center_y": anchor.center_y,
                "width": anchor.width,
                "height": anchor.height,
                "confidence": anchor.confidence,
                "source": anchor.source,
                "position_mode": anchor.position_mode,
            }
        )

    return DetectionResult(
        video_info=video_info,
        events=events,
        anchors=anchors,
        anchor_debug=result.anchor_debug,
        requested_regions=requested_regions,
        mode=mode,
    )


def write_detection_debug(detection_result: DetectionResult, output_path: str | Path) -> None:
    payload = {
        "video": {
            "fps": detection_result.video_info.fps,
            "total_frames": detection_result.video_info.total_frames,
            "width": detection_result.video_info.width,
            "height": detection_result.video_info.height,
            "duration": detection_result.video_info.duration,
        },
        "mode": detection_result.mode,
        "requested_regions": detection_result.requested_regions,
        "anchors": detection_result.anchors,
        "events": [
            {
                "index": event.index,
                "start_time": event.start_time,
                "end_time": event.end_time,
                "start_frame": event.start_frame,
                "end_frame": event.end_frame,
                "text": event.text,
                "confidence": event.confidence,
                "box": event.box,
                "polygon": event.polygon,
            }
            for event in detection_result.events
        ],
        "anchor_debug": detection_result.anchor_debug,
    }
    Path(output_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_detection_debug(input_path: str | Path) -> DetectionResult:
    payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
    video_payload = payload["video"]
    video_info = VideoInfo(
        fps=float(video_payload["fps"]),
        total_frames=int(video_payload["total_frames"]),
        width=int(video_payload["width"]),
        height=int(video_payload["height"]),
        duration=float(video_payload["duration"]),
    )
    events = [
        SubtitleEvent(
            index=int(item["index"]),
            start_time=float(item["start_time"]),
            end_time=float(item["end_time"]),
            start_frame=int(item["start_frame"]),
            end_frame=int(item["end_frame"]),
            text=str(item.get("text", "")),
            confidence=float(item.get("confidence", 0.0)),
            box=tuple(int(v) for v in item["box"]),
            polygon=[(int(x), int(y)) for x, y in item["polygon"]] if item.get("polygon") else None,
        )
        for item in payload.get("events", [])
    ]
    return DetectionResult(
        video_info=video_info,
        events=events,
        anchors=list(payload.get("anchors", [])),
        anchor_debug=payload.get("anchor_debug"),
        requested_regions=payload.get("requested_regions"),
        mode=str(payload.get("mode", "auto")),
    )
