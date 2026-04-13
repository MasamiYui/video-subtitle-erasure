# 实现说明与优化记录

## 1. 文档目的

这份文档记录当前仓库已经实现的能力、这轮优化做了什么、为什么最终选择当前推荐配置，以及如何复现当前的全片结果。

它不是方案草图，而是“当前代码库实际状态”的说明。

## 2. 当前实现概览

当前项目已经具备以下能力：

- 命令行批处理
- FastAPI + 浏览器工作台
- `auto` / `semi-auto` / `manual-fixed` 三种模式
- `detection.json` 调试导出与复用
- 自动评估
- 自动寻优
- 单元测试与 API 测试

核心模块：

- `subtitle_eraser/detection.py`
  - 调用相邻 `subtitle-ocr` 项目做字幕时间与几何定位
  - 支持调节 `merge_threshold`、OCR 检测阈值和预过滤开关
  - 支持把 `detection.json` 重新加载回处理管线
- `subtitle_eraser/masking.py`
  - 构建逐帧字幕 mask
  - 做时序 mask 稳定化，减少描边残留和边界闪烁
- `subtitle_eraser/inpaint.py`
  - `telea` 基线路径
  - 可选 `flow-guided`
  - 对 `flow-guided` 增加了“参考帧一致性约束”，避免在前景复杂区域强行套时序补全
- `subtitle_eraser/evaluation.py`
  - 自动复检残留字幕
  - 评估字幕带外误修改
- `subtitle_eraser/autotune.py`
  - 从长视频抽取代表性短片段
  - 试多组参数
  - 用评估分数排序候选配置

## 3. 这一轮优化的核心结论

### 3.1 先修时间和 mask，再谈更强补全

最开始的问题不是单纯“补全模型不够强”，而是：

- 字幕实际存在的帧数比检测时间轴更长
- 同一帧里的字形 mask 仍然偏紧

因此这轮优化优先做的是：

- 扩张字幕片段的前后边界
- 引入 `mask_temporal_radius`
- 提高 `manual-fixed` 场景下的字幕区域覆盖

### 3.2 `flow-guided` 不是全片默认最优

为了验证这一点，代码里增加了：

- 残留字幕复检
- 字幕带外误改动惩罚
- 片段级 A/B 对比

在 `test_video/我在迪拜等你.mp4` 上，`flow-guided` 的问题是：

- 某些片段确实能把字擦得更狠
- 但人物前景、衣服纹理、玻璃反光和暗场区域更容易出现灰带

所以当前的结论不是“不要 `flow-guided`”，而是：

- 把它作为候选项保留
- 只有在参考帧足够一致时才允许它介入
- 在当前这条长视频上，整片默认仍优先使用 `telea`

### 3.3 自动评估有用，但不是唯一裁判

自动评估已经能帮助发现两个方向：

- 还有多少字被 OCR 重新识别出来
- 为了去字，是否把字幕带外的画面一起改坏了

但它仍然有局限：

- OCR 分数更低，不一定主观更自然
- 某些暗场或高反光镜头，视觉稳定性比 OCR 分数更重要

因此当前策略是：

1. 用自动评估淘汰明显差的配置
2. 对候选配置做关键帧人工抽查
3. 最终选“残字和灰带都能接受”的那组

## 4. 当前推荐配置

对 `test_video/我在迪拜等你.mp4`，当前更稳的整片方案是：

- 模式：`manual-fixed`
- ROI：`0.08,0.72,0.92,0.96`
- 检测：复用已有 `detection.json`，并适度扩张字幕起止帧
- `mask_dilate_x=24`
- `mask_dilate_y=15`
- `mask_temporal_radius=1`
- `event_lead_frames=6`
- `event_trail_frames=18`
- `inpaint_backend=telea`
- `inpaint_context_margin=104`
- `residual_cleanup_passes=0`

对应产物：

- 输出视频：`output/我在迪拜等你_final_candidate.mp4`
- 调试检测：`output/final_candidate_debug/detection.json`

这组配置不是“理论最强”，而是当前这条长视频上“主观效果最稳”的版本。

## 5. 自动评估与自动寻优

### 5.1 自动评估指标

`subtitle_eraser/evaluation.py` 目前计算两类核心指标：

- `residual_ratio`
  - 输出视频中，仍与原字幕时间和位置重叠的 OCR 事件占比
- `spill_score`
  - 字幕带外被误修改的平均差异

综合分数本质上是：

- 先尽量压低残字
- 再避免为了去字把背景改坏

### 5.2 自动寻优做法

`subtitle_eraser/autotune.py` 的流程是：

1. 从长视频里挑代表性短片段
2. 为每组候选参数渲染片段
3. 对片段结果做自动评估
4. 选更优配置

当前候选会变化的方向包括：

- `sample_interval`
- `mask_dilate_x` / `mask_dilate_y`
- `mask_temporal_radius`
- `event_lead_frames` / `event_trail_frames`
- `inpaint_backend`
- `inpaint_context_margin`
- `merge_threshold`

### 5.3 当前的经验性结论

自动寻优非常适合：

- 先筛掉明显不好的参数
- 在短片段上快速比较候选

但在整片生产时，仍建议：

- 用短片段自动寻优给出候选
- 对关键帧再做主观检查
- 必要时复用或手工修正检测时间轴

## 6. 复现方式

### 6.1 运行测试

```bash
PYTHONPATH="../subtitle-ocr:$PWD" \
../subtitle-ocr/.venv/bin/python -m pytest -q
```

### 6.2 运行 Web 原型

```bash
export SUBTITLE_OCR_PROJECT=../subtitle-ocr
../subtitle-ocr/.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

### 6.3 运行整片推荐配置

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
  --segment-gap-frames 4 \
  --context-frames 14 \
  --event-lead-frames 6 \
  --event-trail-frames 18 \
  --residual-cleanup-passes 0 \
  --inpaint-backend telea \
  --inpaint-radius 3 \
  --inpaint-context-margin 104 \
  --max-temporal-references 8 \
  --temporal-consensus 2 \
  --temporal-std-threshold 14 \
  --cleanup-max-coverage 0.045 \
  --merge-threshold 0.72 \
  --ocr-det-db-thresh 0.24 \
  --ocr-det-db-box-thresh 0.4 \
  --disable-prefilter \
  --reuse-detection ./output/full_debug_expanded_v2.json \
  --debug-dir ./output/final_candidate_debug
```

## 7. 已知边界

当前仍未完全解决的场景：

- 暗场里底部高对比字幕留下轻微修补痕迹
- 人物前景刚好压住字幕带时，局部还可能出现不够自然的纹理恢复
- 新做一份全片 OCR 时间轴的代价仍然比较高，长视频调参更适合复用已有 `detection.json`

这也是为什么当前默认建议是：

- 先稳定地擦掉字幕
- 再逐步处理“无痕修复”的更高要求
