# Design

English | [中文](../zh/design.md)

## 1. Overview

```
immich-server container
├── video transcoding / video thumbnails: ffmpeg ──> libavcodec (patches/0001)
│       libx264 / libx265 encoding ──> VPU encoder (x264/x265 when not supported)
│       H.264 / HEVC / MJPEG decoding ──> VPU / JPU decoder (native decoder when not supported)
└── photo previews: MediaRepository.decodeImage() (server/immich)
        JPEG ──> x5thumb: JPU decode ──> 2D GPU resize ──> RGB on the CPU ──┐
        HEIC ──> x5thumb: VPU decodes and stitches tiles ──> 2D GPU ...     ├──> sharp: colour management, rotation, encoding
        other formats / not handled by x5thumb ──> original sharp path ─────┘

immich-machine-learning container (ml/docker/overlay)
        face detection, face recognition, CLIP image encoding ──> BPU (libdnn via ctypes)
        OCR, CLIP text encoding, models without a BPU pack ──> ONNX Runtime CPU (original path)
```

Principles:

- **No change to how immich is configured or calls things.** immich still calls `libx264`, the default
  decoders and `decodeImage()`; the hardware path sits underneath.
- **Every piece falls back.** What the hardware cannot do goes the original software way: correct, just not faster.
- **Same ABI.** The replaced `libavcodec.so.61` and `libavfilter.so.10` export exactly the official symbols,
  which the build script checks.

The RDK X5 codec hardware is only reachable through `libmultimedia.so` (the `hb_mm_mc_*` API); `/dev/video*`
are ISP nodes and FFmpeg's own `h264_v4l2m2m` does not work, hence the patches.

## 2. Video: jellyfin-ffmpeg patch (`server/patches/0001`)

Adds `h264_x5`, `hevc_x5`, `mjpeg_x5` encoders and decoders and makes the usual entry points use them.

**Encoding**: `libx264`/`libx265` try the hardware encoder when opened.

- CBR with `-b:v`, otherwise `crf`/`qp` is used as a constant QP.
- GOP is I-P-P-P with one reference; Wave521 has no B-frames.
- Width and height are padded to multiples of 8 and SPS cropping restores the real size. Portrait videos
  scaled to 720p are often 540 or 406 wide, which the hardware rejects otherwise.
- Lossless, non-4:2:0 and >8-bit stay on x264/x265. x264/x265 presets, tunes and `-x264-params` are ignored
  on the hardware path.
- The encoder decides on hardware only when it is opened; there is no switch back to software mid-stream.

**Decoding**: `h264_x5`/`hevc_x5`/`mjpeg_x5` are registered before the native decoders and become the defaults.

- 10-bit, 4:2:2, `lowres` and key-frame-only decoding (`-skip_frame nointra`, used by immich for video
  thumbnails) go straight to the native decoder.
- If the hardware fails mid-stream, the buffered packets since the last key frame are replayed into the native
  decoder, without repeating frames already output.
- The input queue is sized for the DPB and driven by the queue counters of `hb_mm_mc_get_status()`, not by short
  polling timeouts (the board's kernel runs at HZ=100, so a 1 ms timeout costs 10 ms).
- Output pts, durations and colour range (full range comes out as yuvj420p) match the native decoders; the test
  samples are frame-exact by md5.

`X5_CODEC_DISABLE=1` sends all of these back to software.

## 3. Photos: x5thumb (`server/x5thumb`, `server/immich`)

For a photo preview immich calls `decodeImage(file, { size: 1440 })`: decode the original, rotate by EXIF,
shrink to a short side of 1440, then make the thumbnail and the thumbhash from that. The patch makes it call
`x5thumb <file> 1440` first:

1. **JPEG**: decoded to NV12 by the JPU. Baseline/extended sequential 8-bit only; progressive returns 3.
2. **HEIC**: an iPhone HEIC is a grid of 48 HEVC intra tiles of 512×512. **One** HEVC decoder (the VPU decoder
   from the patch) decodes them one after another and they are stitched together.
   The ffmpeg CLI cannot be used for this: it opens one decoder per tile, about 100 per image, and runs out of
   memory.
   - The colour range is read from the first tile with the native decoder, because the VPU does not export the
     VUI full-range flag.
   - The HEIF `irot` rotation is left to sharp on the small picture; files with `imir` mirroring fall back.
3. The **2D GPU** (nano2D `n2d_filterblit`, bilinear) shrinks to the target size; the CPU converts to RGB.
4. The small picture is wrapped as PNG with the original ICC profile and fed back into immich's own
   `getImageDecodingPipeline()`, so colour management and EXIF orientation are exactly as in the official path.

Return code 3 (not handled) means sharp does it: progressive JPEG, non-8-bit, odd width/height, mirrored HEIF,
non-4:2:0 HEIC, and originals whose short side is not larger than the target. `IMMICH_X5THUMB=` (empty) turns
the whole photo hardware path off.

Some 2D GPU behaviour is not what one would expect; the implementation handles it:

| Behaviour | Handling |
|---|---|
| YUV input is always treated as limited range; `N2D_CSC_SET_FULL_RANGE` only affects the output side | JPEG full range is compressed to limited before the GPU, which runs a BT.601 limited round trip; it is expanded again afterwards |
| YUV→YUV scaling goes through RGB internally | use the source's matrix (BT.601/709) |
| `N2D_RGB888` is really B,G,R, and RGB output is slow | GPU outputs NV12, the CPU converts to RGB |
| Buffers allocated by n2d are uncached for the CPU, per-pixel reads are slow | copy the whole buffer out first |
| `filterblit` with a rotation fails, rotation on the source buffer crashes | rotation is left to sharp (or: scale first, then a same-size `n2d_blit` rotates) |
| The driver has a single context | all processes share the 2D GPU through the file lock `/tmp/x5thumb.n2d.lock` |

The user manual gives 4K as the 2D GPU maximum; downscaling an 8000×6000 input worked in practice.

Most of the quality difference comes from immich resizing in linear light (scRGB) while the 2D GPU resizes in
gamma space; see section 1.3 of the [benchmarks](benchmarks.md).

## 4. Optional: video scaling and rotation on the 2D GPU (`server/patches/0002`)

The `scale` filter (same format and range, 8-bit 4:2:0) and the `transpose` filter (90° clockwise/counter-
clockwise) can run on the 2D GPU, cutting the CPU time of a video to about a third. The GPU operations wait
serially in the filter thread, though, so a single video gets 25–150% slower, and the image defaults to
`X5_N2D_FILTERS=0`. When CPU matters more than time, set `X5_N2D_FILTERS=1` for immich-server in the override
file. Without `/dev/nano2d` swscale is used.

## 5. Machine learning: BPU session (`ml/docker/overlay`)

- New `immich_ml/sessions/bpu/`: calls `libdnn.so` (hb_dnn) directly through ctypes, with no vendor package tied
  to a Python version. Each model directory holds `.bin` files compiled per static input shape and a
  `model.json` describing the outputs, following the convention of immich's RKNN backend: inputs are raw uint8
  NHWC pixels, with mean, scale and channel order compiled into the model.
- `models/base.py`: when a model directory has a `bpu/x5/` pack the BPU session is used, otherwise the original
  ONNX session, model by model. `MACHINE_LEARNING_BPU=false` puts everything back on the CPU.
- The upstream sha256 of every overridden immich file is in `upstream.sha256`; a different base image makes the
  build fail instead of overlaying files on a version they were not written for.

Model conversion (`ml/convert/`, x86 host + OE 1.2.8) only reaches usable accuracy with three things:

1. inputs organised in the layout the BPU actually reads (which differs from what `hb_dnn` reports; measured);
2. ArcFace's PRelu rewritten into basic operators;
3. calibration with the `max` method and full int16.

Missing any of them drops the embedding cosine from 0.99 to 0.1–0.5. OCR (PP-OCRv5_mobile) does not reach
usable accuracy on the BPU and stays on the CPU.

## 6. Hardware evaluated but not used

| Block | Finding |
|---|---|
| VSE | 1080p→720p in 3.8 ms, fastest, but lower quality than the 2D GPU (41 vs 50 dB against lanczos); rejects 12 MP inputs, so no use for photos; cannot rotate while scaling |
| GDC | its 90° rotation resamples through a grid instead of transposing; about 24 dB against an exact transpose; 1080p rotation fails |
| 3D GPU (OpenCL) | not tested. It could resize in linear light with no 4K limit and might close the photo quality gap |
| hardware for video thumbnails | key-frame-only decoding is cheap on the CPU; the hardware must decode every frame and is slower |

## 7. Environment variables

| Variable | Container | Default | Effect |
|---|---|---|---|
| `X5_CODEC_DISABLE` | server | unset | `1`: all codecs and 2D GPU filters back to software |
| `X5_N2D_FILTERS` | server | `0` | `1`: video `scale`/`transpose` on the 2D GPU |
| `IMMICH_X5THUMB` | server | `/usr/local/bin/x5thumb` | empty: photo previews through sharp |
| `LIBMM_FM_PATH` | server | `/usr/local/lib/x5/firmware/` | VPU firmware directory |
| `MACHINE_LEARNING_BPU` | ML | `true` | `false`: all ML on the CPU |
