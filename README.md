# video-subtitle-erasure

Hard subtitle removal for local MP4 videos, with an OCR-assisted CLI pipeline and a browser workbench for ROI selection.

本项目用于本地擦除 MP4 视频里的硬字幕，包含一套 OCR 辅助的命令行处理管线，以及一个可视化的浏览器工作台。

## Status | 当前状态

- Local-first hard subtitle erasure for `mp4`
- Reuses the sibling `subtitle-ocr` project for subtitle timing and geometry
- Includes a browser workbench, async tasks, debug exports, automatic evaluation, and automatic tuning
- Validated on the bundled long-form sample `test_video/我在迪拜等你.mp4`
- Current best full-video recipe is intentionally conservative: expand subtitle timing, stabilize masks temporally, and prefer `telea` over aggressive temporal fill on this dataset

- 面向本地 `mp4` 的硬字幕擦除
- 复用相邻 `subtitle-ocr` 项目做字幕时间和几何定位
- 包含浏览器工作台、异步任务、调试导出、自动评估和自动寻优
- 已在仓库自带长视频样本 `test_video/我在迪拜等你.mp4` 上完成整片验证
- 当前全片最稳的方案是保守型配置：扩张字幕时间轴、做时序 mask 稳定化，并在这个数据集上优先使用 `telea`，避免过激的时序补全留下灰带

## Preview | 效果预览

### Web Workbench | Web 工作台

![Web workbench](./docs/images/web-workbench.png)

### Sample Comparison | 示例对比

The comparison screenshots below were generated from a local clip of `哪吒预告片.mp4`.
Each image is labeled: left is the original frame with hard subtitles, right is the processed frame after subtitle removal.

下面两组对比图使用本地 `哪吒预告片.mp4` 片段生成。
每张图都已标注：左侧是带硬字幕的原始帧，右侧是擦除后的处理结果。

Frame A at about `1.6s` (`虽扛下了天劫`)

帧 A，约 `1.6s`（`虽扛下了天劫`）

![Comparison 1](./docs/images/nezha-compare-01.jpg)

Frame B at about `12.6s` (`海面出现敌情`)

帧 B，约 `12.6s`（`海面出现敌情`）

![Comparison 2](./docs/images/nezha-compare-02.jpg)

## 中文说明

### 项目目标

当前实现重点是先把“本地可跑、效果可持续迭代”的字幕擦除能力搭起来，默认只支持 `mp4` 输入。

当前基线路线：

1. 复用相邻 `subtitle-ocr` 项目的 PaddleOCR 能力，自动定位字幕出现时间和几何区域。
2. 为每一帧生成更细的字幕 mask，并做时序稳定化。
3. 使用 OpenCV 做局部修补，并保留原视频音频。
4. 对结果做自动复检，度量残留字幕和字幕带外误改动。
5. 在短片段上自动搜索更优参数，再决定是否用于整片。

### 当前能力

- 支持命令行批处理
- 支持 FastAPI + 单页 Web 工作台
- 支持 `auto` / `semi-auto` / `manual-fixed`
- 支持在 Web 界面中用静态标注板框选 ROI
- 支持异步任务轮询、结果下载和 `detection.json` 调试输出
- 支持自动评估与自动寻优
- 已补充单元测试和 API 测试

### 当前推荐策略

从这轮迭代后的经验来看，真正影响效果的优先级是：

1. 把字幕时间边界补全，减少“字幕实际存在但没有进入处理区间”的情况
2. 把 mask 做稳，减少描边和抗锯齿残留
3. 再谨慎使用更强的时序补全

在 `test_video/我在迪拜等你.mp4` 这类镜头里，人物前景和高反光背景很多。更激进的 `flow-guided` 虽然在少数片段上 OCR 复检分数更低，但更容易在人物或反光区域留下灰带，所以当前默认文档建议把它视为候选项，而不是全片默认项。

### 安装

推荐直接复用已经验证过的 Python 3.11 环境。

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

如果你已经有 `subtitle-ocr` 的可用环境，也可以直接在那套环境里安装当前项目：

```bash
../subtitle-ocr/.venv/bin/python -m pip install -e .[dev]
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
  --input ./input/demo.mp4 \
  --output ./output/demo_no_sub.mp4 \
  --sample-interval 0.25 \
  --subtitle-ocr-project ../subtitle-ocr \
  --debug-dir ./output/debug
```

推荐的全片命令可以分成两类。

先跑自动寻优，适合短片段或探索参数：

```bash
PYTHONPATH="../subtitle-ocr:$PWD" \
../subtitle-ocr/.venv/bin/python -m subtitle_eraser.cli \
  --input ./test_video/我在迪拜等你.mp4 \
  --output ./output/我在迪拜等你_tuned.mp4 \
  --subtitle-ocr-project ../subtitle-ocr \
  --mode manual-fixed \
  --region 0.08,0.72,0.92,0.96 \
  --position-mode bottom \
  --sample-interval 0.18 \
  --mask-dilate-x 18 \
  --mask-dilate-y 12 \
  --event-lead-frames 4 \
  --event-trail-frames 10 \
  --inpaint-backend telea \
  --disable-prefilter \
  --auto-tune \
  --tune-max-trials 5 \
  --tune-max-rounds 2 \
  --tune-clip-duration 5 \
  --tune-max-clips 2 \
  --debug-dir ./output/tuned_debug
```

如果已经有一份可接受的 `detection.json`，推荐复用并适度扩张时间边界后再跑整片，这通常比重新 OCR 整条长视频更稳定：

```bash
PYTHONPATH="../subtitle-ocr:$PWD" \
../subtitle-ocr/.venv/bin/python -m subtitle_eraser.cli \
  --input ./test_video/我在迪拜等你.mp4 \
  --output ./output/我在迪拜等你_final_candidate.mp4 \
  --subtitle-ocr-project ../subtitle-ocr \
  --mode manual-fixed \
  --region 0.08,0.72,0.92,0.96 \
  --position-mode bottom \
  --sample-interval 0.14 \
  --mask-dilate-x 24 \
  --mask-dilate-y 15 \
  --mask-temporal-radius 1 \
  --event-lead-frames 6 \
  --event-trail-frames 18 \
  --inpaint-backend telea \
  --inpaint-context-margin 104 \
  --disable-prefilter \
  --reuse-detection ./output/full_debug_expanded_v2.json \
  --debug-dir ./output/final_candidate_debug
```

常用参数：

- `--mode`：`auto` / `semi-auto` / `manual-fixed`
- `--sample-interval`：OCR 抽帧间隔，越小越准，但越慢
- `--mask-dilate-x` / `--mask-dilate-y`：mask 扩张像素，控制是否容易残字
- `--mask-temporal-radius`：把相邻帧的 mask 传播到当前帧，减少字幕边缘闪烁和残留
- `--event-lead-frames` / `--event-trail-frames`：扩张字幕出现前后帧，降低时间漏擦
- `--position-mode`：`auto` / `bottom` / `middle` / `top`
- `--reuse-detection`：复用已有 `detection.json`，适合长视频二次重跑
- `--auto-tune`：在短片段上自动搜索更优参数
- `--inpaint-backend`：`telea` 或 `flow-guided`
- `--temporal-consensus` / `--temporal-std-threshold`：约束 `flow-guided` 只在参考帧足够一致时介入

### 自动评估与自动寻优

新增的优化闭环由两个模块组成：

- `subtitle_eraser/evaluation.py`
- `subtitle_eraser/autotune.py`

自动评估会同时计算两类信号：

- `residual_ratio`：输出视频里仍被 OCR 识别到的字幕残留比例
- `spill_score`：字幕带外被意外改坏的像素差惩罚

自动寻优会：

1. 从长视频里选择代表性短片段
2. 在候选参数集合上批量跑擦除
3. 用评估分数排序
4. 选出更优配置，再决定是否用于整片

需要注意的是：OCR 分数不是唯一目标。对某些镜头，分数更低的配置可能主观效果更差，因此当前实现仍然保留“自动筛候选，最终按视觉稳定性收敛”的策略。

### Web 原型

仓库自带一个快速 Web 原型，风格参考了 `subtitle-ocr/static`，目前支持：

- 上传本地 MP4
- 选择处理模式
- 使用播放器定位时间
- 使用独立静态标注板框选 ROI，不拦住视频进度条和控件
- 轮询处理进度并下载结果视频

启动方式：

```bash
export SUBTITLE_OCR_PROJECT=../subtitle-ocr
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

然后打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。

### 测试

单元测试和 API 测试：

```bash
PYTHONPATH="../subtitle-ocr:$PWD" \
../subtitle-ocr/.venv/bin/python -m pytest -q
```

当前测试覆盖了：

- 时间窗口扩张
- ROI 过滤
- `manual-fixed` 的 mask 构建
- 时序 mask 稳定化
- 自动评估与候选片段选择
- `flow-guided` 的回退逻辑
- FastAPI 健康检查和异步任务流

### 输出

- 主输出视频：`--output`
- 调试信息：`debug-dir/detection.json`

### 设计文档

- [视频字幕擦除方案设计](./docs/subtitle-erasure-design.md)
- [实现说明与优化记录](./docs/implementation-notes.md)

## English

### Goal

This project focuses on building a practical local baseline for removing hardcoded subtitles from MP4 videos. The current implementation supports `mp4` input and is designed to be iterated on locally.

Current pipeline:

1. Reuse the neighboring `subtitle-ocr` project to detect subtitle timing and geometry with PaddleOCR.
2. Build finer per-frame subtitle masks and stabilize them temporally.
3. Use OpenCV inpainting for local subtitle removal while keeping the original audio track.
4. Re-check the output automatically for subtitle residue and spill outside the subtitle band.
5. Search better parameters on short clips before running a full video.

### Current Features

- CLI processing pipeline
- FastAPI + single-page browser workbench
- `auto`, `semi-auto`, and `manual-fixed` modes
- ROI selection on a dedicated annotation board
- Async task polling, downloadable results, and `detection.json` debug output
- Automatic evaluation and automatic tuning
- Unit tests and API tests

### Current Guidance

On the bundled long-form sample, the most reliable full-video configuration is conservative:

- expand subtitle timing windows
- stabilize masks across neighboring frames
- prefer `telea`
- treat `flow-guided` as an optional candidate rather than the default

This is a deliberate tradeoff. On reflective or foreground-heavy scenes, aggressive temporal fill can reduce OCR residue while still looking worse to a human reviewer.

### Installation

Using a verified Python 3.11 environment is recommended.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

If you already have a working `subtitle-ocr` environment, you can install this project into that environment instead:

```bash
../subtitle-ocr/.venv/bin/python -m pip install -e .[dev]
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
  --input ./input/demo.mp4 \
  --output ./output/demo_no_sub.mp4 \
  --sample-interval 0.25 \
  --subtitle-ocr-project ../subtitle-ocr \
  --debug-dir ./output/debug
```

Use automatic tuning on short clips when exploring parameters:

```bash
PYTHONPATH="../subtitle-ocr:$PWD" \
../subtitle-ocr/.venv/bin/python -m subtitle_eraser.cli \
  --input ./test_video/我在迪拜等你.mp4 \
  --output ./output/我在迪拜等你_tuned.mp4 \
  --subtitle-ocr-project ../subtitle-ocr \
  --mode manual-fixed \
  --region 0.08,0.72,0.92,0.96 \
  --position-mode bottom \
  --sample-interval 0.18 \
  --mask-dilate-x 18 \
  --mask-dilate-y 12 \
  --event-lead-frames 4 \
  --event-trail-frames 10 \
  --inpaint-backend telea \
  --disable-prefilter \
  --auto-tune \
  --tune-max-trials 5 \
  --tune-max-rounds 2 \
  --tune-clip-duration 5 \
  --tune-max-clips 2 \
  --debug-dir ./output/tuned_debug
```

For long videos, reusing a known-good detection timeline is often more practical:

```bash
PYTHONPATH="../subtitle-ocr:$PWD" \
../subtitle-ocr/.venv/bin/python -m subtitle_eraser.cli \
  --input ./test_video/我在迪拜等你.mp4 \
  --output ./output/我在迪拜等你_final_candidate.mp4 \
  --subtitle-ocr-project ../subtitle-ocr \
  --mode manual-fixed \
  --region 0.08,0.72,0.92,0.96 \
  --position-mode bottom \
  --sample-interval 0.14 \
  --mask-dilate-x 24 \
  --mask-dilate-y 15 \
  --mask-temporal-radius 1 \
  --event-lead-frames 6 \
  --event-trail-frames 18 \
  --inpaint-backend telea \
  --inpaint-context-margin 104 \
  --disable-prefilter \
  --reuse-detection ./output/full_debug_expanded_v2.json \
  --debug-dir ./output/final_candidate_debug
```

Useful flags:

- `--mode`: `auto`, `semi-auto`, or `manual-fixed`
- `--sample-interval`: OCR sampling interval in seconds
- `--mask-dilate-x` / `--mask-dilate-y`: subtitle mask dilation
- `--mask-temporal-radius`: propagate subtitle masks from nearby frames
- `--event-lead-frames` / `--event-trail-frames`: extend subtitle windows to reduce timing misses
- `--position-mode`: `auto`, `bottom`, `middle`, or `top`
- `--reuse-detection`: reuse an existing `detection.json`
- `--auto-tune`: search candidate configs on short clips
- `--inpaint-backend`: `telea` or `flow-guided`
- `--temporal-consensus` / `--temporal-std-threshold`: keep temporal fill conservative

### Web Prototype

The repository includes a fast browser workbench inspired by `subtitle-ocr/static`. It currently supports:

- local MP4 upload
- processing mode selection
- video playback for timeline navigation
- ROI annotation on a separate static frame board, without blocking video controls
- async progress polling and result download

Run it with:

```bash
export SUBTITLE_OCR_PROJECT=../subtitle-ocr
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000).

### Tests

Run the test suite with:

```bash
PYTHONPATH="../subtitle-ocr:$PWD" \
../subtitle-ocr/.venv/bin/python -m pytest -q
```

Current tests cover:

- subtitle window expansion
- ROI filtering
- `manual-fixed` mask generation
- temporal mask stabilization
- automatic evaluation and clip selection
- `flow-guided` fallback behavior
- FastAPI health checks and async task flow

### Outputs

- Main output video: `--output`
- Debug metadata: `debug-dir/detection.json`

### Design Document

- [Subtitle Erasure Design](./docs/subtitle-erasure-design.md)
- [Implementation Notes](./docs/implementation-notes.md)
