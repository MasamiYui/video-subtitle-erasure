# video-subtitle-erasure

Hard subtitle removal for local MP4 videos, with an OCR-assisted CLI pipeline and a browser workbench for ROI selection.

本项目用于本地擦除 MP4 视频里的硬字幕，包含一套 OCR 辅助的命令行处理管线，以及一个可视化的浏览器工作台。

## Preview | 效果预览

### Web Workbench | Web 工作台

![Web workbench](./docs/images/web-workbench.png)

### Sample Comparison | 示例对比

The comparison screenshots below were generated from `哪吒预告片.mp4` and show before/after subtitle removal on two frames.

下面两组对比图使用 `哪吒预告片.mp4` 生成，展示了两帧字幕擦除前后的效果。

![Comparison 1](./docs/images/nezha-compare-026.jpg)
![Comparison 2](./docs/images/nezha-compare-078.jpg)

## 中文说明

### 项目目标

当前实现重点是先把“本地可跑、效果可持续迭代”的字幕擦除能力搭起来，默认只支持 `mp4` 输入。

当前基线路线：

1. 复用相邻 `subtitle-ocr` 项目的 PaddleOCR 能力，自动定位字幕出现时间和几何区域。
2. 为每一帧生成更细的字幕 mask。
3. 使用 OpenCV 做局部修补，并保留原视频音频。

### 当前能力

- 支持命令行批处理
- 支持 FastAPI + 单页 Web 工作台
- 支持 `auto` / `semi-auto` / `manual-fixed`
- 支持在 Web 界面中用静态标注板框选 ROI
- 支持异步任务轮询、结果下载和 `detection.json` 调试输出
- 已补充单元测试和 API 测试

### 安装

推荐直接复用已经验证过的 Python 3.11 环境。

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

如果你已经有 `subtitle-ocr` 的可用环境，也可以直接在那套环境里安装当前项目：

```bash
cd /Users/masamiyui/OpenSoureProjects/Forks/video-subtitle-erasure
/Users/masamiyui/OpenSoureProjects/Forks/subtitle-ocr/.venv/bin/python -m pip install -e .[dev]
```

### 依赖 `subtitle-ocr`

当前仓库不会重新复制一套字幕检测代码，而是默认桥接到相邻目录的 `subtitle-ocr`：

- 默认查找路径：`../subtitle-ocr`
- 也可以显式传入：`--subtitle-ocr-project /absolute/path/to/subtitle-ocr`
- 或者设置环境变量：`SUBTITLE_OCR_PROJECT=/absolute/path/to/subtitle-ocr`

### CLI 用法

先对一个片段验证效果：

```bash
subtitle-erase \
  --input "/Users/masamiyui/OpenSoureProjects/Forks/video-subtitle-erasure/test_video/我在迪拜等你.mp4" \
  --output "/Users/masamiyui/OpenSoureProjects/Forks/video-subtitle-erasure/output/demo_no_sub.mp4" \
  --sample-interval 0.25 \
  --subtitle-ocr-project "/Users/masamiyui/OpenSoureProjects/Forks/subtitle-ocr" \
  --debug-dir "/Users/masamiyui/OpenSoureProjects/Forks/video-subtitle-erasure/output/debug"
```

常用参数：

- `--mode`：`auto` / `semi-auto` / `manual-fixed`
- `--sample-interval`：OCR 抽帧间隔，越小越准，但越慢
- `--mask-dilate-x` / `--mask-dilate-y`：mask 扩张像素，控制是否容易残字
- `--event-lead-frames` / `--event-trail-frames`：扩张字幕出现前后帧，降低时间漏擦
- `--position-mode`：`auto` / `bottom` / `middle` / `top`

### Web 原型

仓库自带一个快速 Web 原型，风格参考了 `subtitle-ocr/static`，目前支持：

- 上传本地 MP4
- 选择处理模式
- 使用播放器定位时间
- 使用独立静态标注板框选 ROI，不拦住视频进度条和控件
- 轮询处理进度并下载结果视频

启动方式：

```bash
PYTHONPATH="/Users/masamiyui/OpenSoureProjects/Forks/subtitle-ocr:/Users/masamiyui/OpenSoureProjects/Forks/video-subtitle-erasure" \
/Users/masamiyui/OpenSoureProjects/Forks/subtitle-ocr/.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

然后打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。

### 测试

单元测试和 API 测试：

```bash
PYTHONPATH="/Users/masamiyui/OpenSoureProjects/Forks/subtitle-ocr:$PWD" \
/Users/masamiyui/OpenSoureProjects/Forks/subtitle-ocr/.venv/bin/python -m pytest -q
```

当前测试覆盖了：

- 时间窗口扩张
- ROI 过滤
- `manual-fixed` 的 mask 构建
- FastAPI 健康检查和异步任务流

### 输出

- 主输出视频：`--output`
- 调试信息：`debug-dir/detection.json`

### 设计文档

- [视频字幕擦除方案设计](./docs/subtitle-erasure-design.md)

## English

### Goal

This project focuses on building a practical local baseline for removing hardcoded subtitles from MP4 videos. The current implementation supports `mp4` input and is designed to be iterated on locally.

Current pipeline:

1. Reuse the neighboring `subtitle-ocr` project to detect subtitle timing and geometry with PaddleOCR.
2. Build finer per-frame subtitle masks.
3. Use OpenCV inpainting for local subtitle removal while keeping the original audio track.

### Current Features

- CLI processing pipeline
- FastAPI + single-page browser workbench
- `auto`, `semi-auto`, and `manual-fixed` modes
- ROI selection on a dedicated annotation board
- Async task polling, downloadable results, and `detection.json` debug output
- Unit tests and API tests

### Installation

Using a verified Python 3.11 environment is recommended.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

If you already have a working `subtitle-ocr` environment, you can install this project into that environment instead:

```bash
cd /Users/masamiyui/OpenSoureProjects/Forks/video-subtitle-erasure
/Users/masamiyui/OpenSoureProjects/Forks/subtitle-ocr/.venv/bin/python -m pip install -e .[dev]
```

### `subtitle-ocr` Dependency

This repository does not duplicate subtitle detection logic. It bridges to the sibling `subtitle-ocr` project by default:

- Default lookup path: `../subtitle-ocr`
- Or pass `--subtitle-ocr-project /absolute/path/to/subtitle-ocr`
- Or set `SUBTITLE_OCR_PROJECT=/absolute/path/to/subtitle-ocr`

### CLI Usage

Run the pipeline on a short clip first:

```bash
subtitle-erase \
  --input "/Users/masamiyui/OpenSoureProjects/Forks/video-subtitle-erasure/test_video/我在迪拜等你.mp4" \
  --output "/Users/masamiyui/OpenSoureProjects/Forks/video-subtitle-erasure/output/demo_no_sub.mp4" \
  --sample-interval 0.25 \
  --subtitle-ocr-project "/Users/masamiyui/OpenSoureProjects/Forks/subtitle-ocr" \
  --debug-dir "/Users/masamiyui/OpenSoureProjects/Forks/video-subtitle-erasure/output/debug"
```

Useful flags:

- `--mode`: `auto`, `semi-auto`, or `manual-fixed`
- `--sample-interval`: OCR sampling interval in seconds
- `--mask-dilate-x` / `--mask-dilate-y`: subtitle mask dilation
- `--event-lead-frames` / `--event-trail-frames`: extend subtitle windows to reduce timing misses
- `--position-mode`: `auto`, `bottom`, `middle`, or `top`

### Web Prototype

The repository includes a fast browser workbench inspired by `subtitle-ocr/static`. It currently supports:

- local MP4 upload
- processing mode selection
- video playback for timeline navigation
- ROI annotation on a separate static frame board, without blocking video controls
- async progress polling and result download

Run it with:

```bash
PYTHONPATH="/Users/masamiyui/OpenSoureProjects/Forks/subtitle-ocr:/Users/masamiyui/OpenSoureProjects/Forks/video-subtitle-erasure" \
/Users/masamiyui/OpenSoureProjects/Forks/subtitle-ocr/.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000).

### Tests

Run the test suite with:

```bash
PYTHONPATH="/Users/masamiyui/OpenSoureProjects/Forks/subtitle-ocr:$PWD" \
/Users/masamiyui/OpenSoureProjects/Forks/subtitle-ocr/.venv/bin/python -m pytest -q
```

Current tests cover:

- subtitle window expansion
- ROI filtering
- `manual-fixed` mask generation
- FastAPI health checks and async task flow

### Outputs

- Main output video: `--output`
- Debug metadata: `debug-dir/detection.json`

### Design Document

- [Subtitle Erasure Design](./docs/subtitle-erasure-design.md)
