from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from subtitle_eraser.models import DetectionResult, VideoInfo
from subtitle_eraser.web import create_app


def fake_erase_subtitles(input_path: str, output_path: str, config, progress_callback=None) -> DetectionResult:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"fake-mp4")

    if config.debug_dir:
        debug_path = Path(config.debug_dir) / "detection.json"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text('{"events": []}', encoding="utf-8")

    if progress_callback is not None:
        progress_callback("detecting", 20, "fake detecting")
        progress_callback("erasing", 80, "fake erasing")
        progress_callback("completed", 100, "fake completed")

    return DetectionResult(
        video_info=VideoInfo(fps=25.0, total_frames=50, width=960, height=416, duration=2.0),
        events=[],
        anchors=[],
        anchor_debug={},
        requested_regions=config.requested_regions,
        mode=config.mode,
    )


def test_health_and_config_endpoints(tmp_path: Path) -> None:
    app = create_app(work_dir=tmp_path)
    with TestClient(app) as client:
        health = client.get("/health")
        config = client.get("/api/v1/erase/upload/config")

    assert health.status_code == 200
    assert health.json()["status"] == "healthy"
    assert config.status_code == 200
    assert "manual-fixed" in config.json()["modes"]


def test_manual_fixed_upload_flow(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("subtitle_eraser.tasks.erase_subtitles", fake_erase_subtitles)
    app = create_app(work_dir=tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/erase/extract/async",
            files={"file": ("demo.mp4", b"fake-video", "video/mp4")},
            data={
                "mode": "manual-fixed",
                "regions": json.dumps([{"x": 0.12, "y": 0.74, "width": 0.72, "height": 0.18}]),
            },
        )
        assert response.status_code == 200
        payload = response.json()
        task_id = payload["task_id"]

        for _ in range(30):
            status = client.get(f"/api/v1/erase/status/{task_id}")
            assert status.status_code == 200
            body = status.json()
            if body["status"] == "completed":
                break
            time.sleep(0.05)
        else:
            raise AssertionError("task did not complete")

        download = client.get(f"/api/v1/erase/download/{task_id}")
        debug = client.get(f"/api/v1/erase/debug/{task_id}")

    assert download.status_code == 200
    assert download.content == b"fake-mp4"
    assert debug.status_code == 200
