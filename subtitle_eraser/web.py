from __future__ import annotations

import json
import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from subtitle_eraser.regions import NormalizedRegion, clamp_normalized_region
from subtitle_eraser.tasks import TaskManager
from subtitle_eraser.video import PipelineConfig

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_RUNTIME_DIR = ROOT_DIR / "runtime"


def _parse_regions(raw_regions: str | None) -> list[NormalizedRegion]:
    if not raw_regions:
        return []

    payload = json.loads(raw_regions)
    if not isinstance(payload, list):
        raise ValueError("regions must be a list")

    regions: list[NormalizedRegion] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("region must be an object")
        x = float(item.get("x", 0.0))
        y = float(item.get("y", 0.0))
        width = float(item.get("width", 0.0))
        height = float(item.get("height", 0.0))
        if width <= 0 or height <= 0:
            continue
        regions.append(clamp_normalized_region((x, y, x + width, y + height)))
    return regions


def create_app(
    work_dir: Path | None = None,
    subtitle_ocr_project: str | None = None,
) -> FastAPI:
    manager = TaskManager(work_dir=work_dir or DEFAULT_RUNTIME_DIR, subtitle_ocr_project=subtitle_ocr_project)
    manager.ensure_dirs()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        manager.shutdown()

    app = FastAPI(
        title="Video Subtitle Erasure",
        version="0.2.0",
        description="Local MP4 subtitle erasure service",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.task_manager = manager

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", response_class=FileResponse)
    async def root() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy", "service": "video-subtitle-erasure"}

    @app.get("/api/v1/erase/upload/config")
    async def upload_config() -> dict[str, object]:
        return {
            "accept": [".mp4"],
            "modes": ["auto", "semi-auto", "manual-fixed"],
            "max_regions": 3,
            "defaults": {
                "mode": "auto",
                "language": "ch",
                "sampleInterval": 0.25,
                "positionMode": "bottom",
                "roiBottomRatio": 0.34,
                "maskDilateX": 14,
                "maskDilateY": 10,
                "segmentGapFrames": 3,
                "contextFrames": 12,
                "eventLeadFrames": 2,
                "eventTrailFrames": 8,
            },
        }

    @app.post("/api/v1/erase/extract/async")
    async def create_task(
        file: UploadFile = File(...),
        mode: str = Form("auto"),
        language: str = Form("ch"),
        sample_interval: float = Form(0.25),
        position_mode: str = Form("bottom"),
        roi_bottom_ratio: float = Form(0.34),
        mask_dilate_x: int = Form(14),
        mask_dilate_y: int = Form(10),
        segment_gap_frames: int = Form(3),
        context_frames: int = Form(12),
        event_lead_frames: int = Form(2),
        event_trail_frames: int = Form(8),
        regions: str | None = Form(None),
    ) -> dict[str, object]:
        if not file.filename or Path(file.filename).suffix.lower() != ".mp4":
            raise HTTPException(status_code=400, detail="Only MP4 files are supported")
        if mode not in {"auto", "semi-auto", "manual-fixed"}:
            raise HTTPException(status_code=400, detail="Unsupported mode")

        try:
            requested_regions = _parse_regions(regions)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if mode == "manual-fixed" and not requested_regions:
            raise HTTPException(status_code=400, detail="manual-fixed mode requires at least one region")

        manager: TaskManager = app.state.task_manager
        status = manager.create_task(file.filename)
        input_path, output_path, debug_path = manager.build_paths(status.task_id, file.filename)

        with input_path.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)

        manager.start(
            status.task_id,
            input_path=input_path,
            output_path=output_path,
            debug_path=debug_path,
            config=PipelineConfig(
                subtitle_ocr_project=subtitle_ocr_project,
                mode=mode,
                language=language,
                sample_interval=sample_interval,
                position_mode=position_mode,
                roi_bottom_ratio=roi_bottom_ratio,
                requested_regions=requested_regions or None,
                mask_dilate_x=mask_dilate_x,
                mask_dilate_y=mask_dilate_y,
                segment_gap_frames=segment_gap_frames,
                context_frames=context_frames,
                event_lead_frames=event_lead_frames,
                event_trail_frames=event_trail_frames,
            ),
        )
        return manager.serialize(status.task_id) or {}

    @app.get("/api/v1/erase/status/{task_id}")
    async def task_status(task_id: str) -> dict[str, object]:
        payload = app.state.task_manager.serialize(task_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return payload

    @app.get("/api/v1/erase/download/{task_id}")
    async def download(task_id: str) -> FileResponse:
        status = app.state.task_manager.get(task_id)
        if status is None:
            raise HTTPException(status_code=404, detail="Task not found")
        if status.status != "completed" or not status.output_path:
            raise HTTPException(status_code=409, detail="Task is not ready")
        path = Path(status.output_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Output file missing")
        return FileResponse(path, media_type="video/mp4", filename=path.name)

    @app.get("/api/v1/erase/debug/{task_id}")
    async def debug(task_id: str) -> FileResponse:
        status = app.state.task_manager.get(task_id)
        if status is None:
            raise HTTPException(status_code=404, detail="Task not found")
        if not status.debug_path:
            raise HTTPException(status_code=404, detail="Debug file not found")
        path = Path(status.debug_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Debug file not found")
        return FileResponse(path, media_type="application/json", filename=path.name)

    return app


app = create_app()
