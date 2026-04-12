from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class VideoInfo:
    fps: float
    total_frames: int
    width: int
    height: int
    duration: float


@dataclass(slots=True)
class SubtitleEvent:
    index: int
    start_time: float
    end_time: float
    start_frame: int
    end_frame: int
    text: str
    confidence: float
    box: tuple[int, int, int, int]
    polygon: list[tuple[int, int]] | None = None


@dataclass(slots=True)
class TaskStatus:
    task_id: str
    status: str
    progress: int
    stage: str
    message: str = ""
    input_filename: str | None = None
    output_path: str | None = None
    debug_path: str | None = None
    error: str | None = None
    result_url: str | None = None
    debug_url: str | None = None


@dataclass(slots=True)
class DetectionResult:
    video_info: VideoInfo
    events: list[SubtitleEvent]
    anchors: list[dict]
    anchor_debug: dict | None = None
    requested_regions: list[tuple[float, float, float, float]] | None = None
    mode: str = "auto"


@dataclass(slots=True)
class ProcessingSegment:
    start_frame: int
    end_frame: int
    context_start: int
    context_end: int
    frame_events: dict[int, list[SubtitleEvent]] = field(default_factory=dict)
