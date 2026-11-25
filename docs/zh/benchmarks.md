# 性能数据

[English](../en/benchmarks.md) | 中文

所有数字都是在 RDK X5 上实测的，测试日期 2026 年 11 月，系统 3.5.0，immich v3.4.2。
"4 GB 板"可用内存约 3 GB，"8 GB 板"约 7 GB（其余为 ION 等预留）。
引用块里是测试脚本的原始输出。

## 1. 照片缩略图

### 1.1 immich 缩略图任务

8 GB 板，从图库随机选 200 张照片（160 张 JPEG、40 张 HEIC，均不小于 2 MP），用
`POST /api/assets/jobs`（`regenerate-thumbnail`）重建，缩略图并发 4。`tools/thumb_watch.sh`
统计从写出第一个到最后一个缩略图文件的时间：

```
files 398 in 349 s (first to last write), avg cpu 73%, min MemAvailable 3992 MB, max ion 4 MB
files 400 in 134 s (first to last write), avg cpu 79%, min MemAvailable 4061 MB, max ion 28 MB
```

第一行是官方镜像（照片走 sharp/libvips），第二行是本方案。每张照片生成预览图和缩略图两个文件。
按 8 核折算，CPU 时间约 2040 核·秒降到 850 核·秒。

4 GB 板，测试实例上 90 个资产（80 张照片 + 10 个视频），缩略图并发 1：官方镜像 489 s，本方案 171 s。

### 1.2 单张预览图（decodeImage，目标 1440）

4 GB 板，单线程，在 immich 自己的 `MediaRepository` 上测量，CPU 时间包含 `x5thumb` 子进程：

| 样本（≥2 MP） | 张数 | 官方 CPU | 本方案 CPU |
|---|---|---|---|
| JPEG，无 ICC | 26 | 5023 ms | 1157 ms |
| JPEG 带 ICC + HEIC | 31 | 9578 ms | 1630 ms |
| 其中 HEIC | 15 | 7701～9914 ms | 1342～1948 ms |

官方路径的 CPU 主要花在解码+旋转（JPEG 28%、HEIC 57%）和缩放到 1440（JPEG 51%、HEIC 29%），
编码 WebP/JPEG 和 thumbhash 只占约五分之一。

### 1.3 画质

与官方输出的 RGB PSNR 中位数：无 ICC 的 JPEG 39.8 dB，带 ICC 的 JPEG 与 HEIC 38.9 dB，最低一张
20.3 dB（细节极密的照片）。差异主要来自缩放发生的空间：官方在线性光（scRGB）里缩放，2D GPU 在
gamma 空间缩放。只用软件把缩放改到 gamma 空间，与官方的差距也有 44～51 dB，那张最差的照片同样只有
29.6 dB。作为参照，sharp 自己把缩放核从 lanczos3 换成 mitchell，与官方的 PSNR 中位数约 42 dB。

## 2. 视频

### 2.1 immich 转码任务

4 GB 板，测试实例上 10 个视频（含 2 个 10-bit HDR、1 个 mpeg4），转码并发 1：

| 镜像 | 用时 | 平均 CPU |
|---|---|---|
| 官方 | 126 s | 70% |
| 本方案 | 91 s | 53% |

10-bit HDR 的 tonemap 和 mpeg4 软解仍在 CPU 上。

### 2.2 单个 1080p 视频

4 GB 板，immich 的命令（`-c:v h264 -preset ultrafast -crf 23`，缩放到 720p），竖屏视频 24.8 s / 742 帧，
横屏视频 32.5 s / 975 帧：

```
swscale    portrait 1080p->720 (rotate+scale) wall  9.43s  cpu  489%  (cpu 46.1s)
swscale    landscape 1080p->720 (scale)       wall  7.42s  cpu  487%  (cpu 36.1s)
stock      portrait 1080p->720 (rotate+scale) wall 20.69s  cpu  694%  (cpu 143.6s)
stock      landscape 1080p->720 (scale)       wall 22.45s  cpu  773%  (cpu 173.6s)
```

`swscale` 是本方案默认配置（VPU 编解码，缩放旋转仍在 CPU），`stock` 是官方镜像。

可选的 2D GPU 缩放旋转（`X5_N2D_FILTERS=1`）CPU 更低，但单个视频更慢，所以默认关闭：

```
gpu        portrait 1080p->720 (rotate+scale) wall 23.16s  cpu   70%  (cpu 16.2s)
gpu        landscape 1080p->720 (scale)       wall 11.56s  cpu   91%  (cpu 10.5s)
```

### 2.3 编解码器本身

硬件解码与 FFmpeg 原生解码逐帧 md5 一致（10 个真实样本，含 H.264/HEVC、full range、变帧率）。
8-bit H.264/HEVC 走 VPU，10-bit 和非 4:2:0 自动走软件。

### 2.4 视频缩略图

immich 生成视频缩略图时只解关键帧（`-skip_frame nointra`），CPU 本来就轻；硬件必须逐帧解码，
实测反而慢（10 个视频 20 s 变 72 s）。本方案在这种情况下自动使用原生解码器，与官方一致（21 s）。

## 3. 机器学习（BPU）

4 GB 板，两个容器用同一镜像，一个映射 BPU，一个设 `MACHINE_LEARNING_BPU=false`，先调用一次加载模型，
再取 5 次平均（含 HTTP、解码、前后处理）：

```
port 3003 face  mean 0.350 s (5 runs)
port 3003 clip  mean 0.179 s (5 runs)
port 3003 ocr   mean 2.842 s (5 runs)
port 3004 face  mean 13.068 s (5 runs)
port 3004 clip  mean 1.028 s (5 runs)
port 3004 ocr   mean 2.817 s (5 runs)
faces e2e_resp_3003_face.json: 7, e2e_resp_3004_face.json: 7
matched IoU mean 0.993 min 0.974; embedding cos mean 0.9988 min 0.9980
clip embedding BPU vs CPU cos 0.99555
```

3003 是 BPU，3004 是 CPU；face 是一张 7 人合影的检测+识别。OCR 两边都在 CPU 上。

单模型精度（与 immich 的 ONNX fp32 输出对比）：

| 模型 | 评测集 | 精度 | BPU 推理耗时 |
|---|---|---|---|
| 人脸检测 640×640 | 30 张图 | 阈值 0.7：召回 1.000，精确率 0.986 | 87.75 ms |
| 人脸识别 112×112 | 100 张对齐人脸 | 余弦均值 0.99915，最小 0.98482 | 19.76 ms |
| CLIP visual 224×224 | 60 张图 | 余弦均值 0.99117，最小 0.97558 | 104.37 ms |

CLIP 以图搜图有约 10% 的样本最近邻发生变化（前两名相似度接近的情况），语义搜索排序会有小幅差异。
