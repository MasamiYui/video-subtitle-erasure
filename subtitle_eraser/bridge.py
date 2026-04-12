from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def resolve_subtitle_ocr_project(explicit_path: str | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path).expanduser().resolve())

    env_path = os.environ.get("SUBTITLE_OCR_PROJECT")
    if env_path:
        candidates.append(Path(env_path).expanduser().resolve())

    default_path = (Path(__file__).resolve().parents[1] / ".." / "subtitle-ocr").resolve()
    candidates.append(default_path)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_subtitle_service(subtitle_ocr_project: str | None = None):
    try:
        module = importlib.import_module("services.subtitle_service")
        return module.SubtitleService
    except ModuleNotFoundError:
        project_path = resolve_subtitle_ocr_project(subtitle_ocr_project)
        if project_path is None:
            raise RuntimeError(
                "Unable to import subtitle-ocr. "
                "Pass --subtitle-ocr-project or set SUBTITLE_OCR_PROJECT."
            ) from None

        project_str = str(project_path)
        if project_str not in sys.path:
            sys.path.insert(0, project_str)

        module = importlib.import_module("services.subtitle_service")
        return module.SubtitleService
