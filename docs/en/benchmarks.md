# Benchmarks

English | [中文](../zh/benchmarks.md)

All numbers were measured on RDK X5 boards in November 2026, system 3.5.0, immich v3.4.2.
A "4 GB board" has about 3 GB usable, an "8 GB board" about 7 GB (the rest is reserved for ION etc.).
Code blocks are raw output of the test scripts.

## 1. Photo thumbnails

### 1.1 immich thumbnail job

8 GB board, 200 photos picked at random from the library (160 JPEG, 40 HEIC, all at least 2 MP),
regenerated through `POST /api/assets/jobs` (`regenerate-thumbnail`), thumbnail concurrency 4.
`tools/thumb_watch.sh` measures the time from the first to the last thumbnail file written:

```
files 398 in 349 s (first to last write), avg cpu 73%, min MemAvailable 3992 MB, max ion 4 MB
files 400 in 134 s (first to last write), avg cpu 79%, min MemAvailable 4061 MB, max ion 28 MB
```

The first line is the official image (photos go through sharp/libvips), the second this project. Each photo
produces two files, a preview and a thumbnail. Over 8 cores, CPU time drops from about 2040 to 850 core-seconds.

4 GB board, 90 assets on a test instance (80 photos + 10 videos), thumbnail concurrency 1: official image
489 s, this project 171 s.

### 1.2 Single preview (decodeImage, target 1440)

4 GB board, single thread, measured on immich's own `MediaRepository`; CPU time includes the `x5thumb`
child process:

| Samples (≥2 MP) | Count | Official CPU | This project CPU |
|---|---|---|---|
| JPEG without ICC | 26 | 5023 ms | 1157 ms |
| JPEG with ICC + HEIC | 31 | 9578 ms | 1630 ms |
| of which HEIC | 15 | 7701–9914 ms | 1342–1948 ms |

In the official path the CPU time goes into decoding + rotation (JPEG 28%, HEIC 57%) and resizing to 1440
(JPEG 51%, HEIC 29%); encoding WebP/JPEG and the thumbhash take about one fifth.

### 1.3 Quality

Median RGB PSNR against the official output: 39.8 dB for JPEG without ICC, 38.9 dB for JPEG with ICC and
HEIC, 20.3 dB for the worst photo (extremely fine detail). The difference comes mostly from where the
resizing happens: the official path resizes in linear light (scRGB), the 2D GPU in gamma space. Moving only
the resize into gamma space in software already gives 44–51 dB against the official output, and the same
worst photo only 29.6 dB. For reference, sharp itself with mitchell instead of lanczos3 differs from the
official output by a median of about 42 dB.

## 2. Video

### 2.1 immich transcoding job

4 GB board, 10 videos on a test instance (including two 10-bit HDR and one mpeg4), transcoding concurrency 1:

| Image | Time | Average CPU |
|---|---|---|
| Official | 126 s | 70% |
| This project | 91 s | 53% |

Tone mapping of the 10-bit HDR videos and mpeg4 decoding still run on the CPU.

### 2.2 One 1080p video

4 GB board, immich's command (`-c:v h264 -preset ultrafast -crf 23`, scaled to 720p); portrait video
24.8 s / 742 frames, landscape 32.5 s / 975 frames:

```
swscale    portrait 1080p->720 (rotate+scale) wall  9.43s  cpu  489%  (cpu 46.1s)
swscale    landscape 1080p->720 (scale)       wall  7.42s  cpu  487%  (cpu 36.1s)
stock      portrait 1080p->720 (rotate+scale) wall 20.69s  cpu  694%  (cpu 143.6s)
stock      landscape 1080p->720 (scale)       wall 22.45s  cpu  773%  (cpu 173.6s)
```

`swscale` is this project's default (VPU codecs, scaling and rotation on the CPU), `stock` the official image.

The optional 2D GPU scaling and rotation (`X5_N2D_FILTERS=1`) uses less CPU but makes a single video slower,
so it is off by default:

```
gpu        portrait 1080p->720 (rotate+scale) wall 23.16s  cpu   70%  (cpu 16.2s)
gpu        landscape 1080p->720 (scale)       wall 11.56s  cpu   91%  (cpu 10.5s)
```

### 2.3 The codecs

Hardware decoding gives the same per-frame md5 as FFmpeg's native decoders (10 real samples including
H.264/HEVC, full range and variable frame rate). 8-bit H.264/HEVC goes to the VPU; 10-bit and non-4:2:0
switch to software automatically.

### 2.4 Video thumbnails

immich decodes only key frames for video thumbnails (`-skip_frame nointra`), which is cheap on the CPU;
the hardware has to decode every frame and was slower (10 videos: 20 s became 72 s). In that case this
project uses the native decoder, matching the official timing (21 s).

## 3. Machine learning (BPU)

4 GB board, two containers from the same image, one with the BPU mapped and one with
`MACHINE_LEARNING_BPU=false`; one warm-up call, then the mean of 5 calls (including HTTP, decoding,
pre- and post-processing):

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

Port 3003 is the BPU, 3004 the CPU; "face" is detection + recognition on a group photo of 7. OCR runs on the
CPU in both.

Per-model accuracy against immich's ONNX fp32 outputs:

| Model | Eval set | Accuracy | BPU inference |
|---|---|---|---|
| Face detection 640×640 | 30 images | threshold 0.7: recall 1.000, precision 0.986 | 87.75 ms |
| Face recognition 112×112 | 100 aligned faces | cosine mean 0.99915, min 0.98482 | 19.76 ms |
| CLIP visual 224×224 | 60 images | cosine mean 0.99117, min 0.97558 | 104.37 ms |

For image-to-image search about 10% of the samples get a different nearest neighbour (cases where the top two
are nearly tied), so semantic search ranking changes slightly.
