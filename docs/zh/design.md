# 设计说明

[English](../en/design.md) | 中文

## 1. 总览

```
immich-server 容器
├── 视频转码 / 视频缩略图: ffmpeg ──> libavcodec (patches/0001)
│       libx264 / libx265 编码 ──> VPU 编码（不支持时回退 x264/x265）
│       H.264 / HEVC / MJPEG 解码 ──> VPU / JPU 解码（不支持时回退原生解码器）
└── 照片预览: MediaRepository.decodeImage() (server/immich)
        JPEG ──> x5thumb: JPU 解码 ──> 2D GPU 缩放 ──> CPU 转 RGB ──┐
        HEIC ──> x5thumb: VPU 逐块解码拼接 ──> 2D GPU 缩放 ──> ...  ├──> sharp: 色彩管理、旋转、编码
        其他格式 / x5thumb 不处理 ──> sharp 原路径 ────────────────┘

immich-machine-learning 容器 (ml/docker/overlay)
        人脸检测、人脸识别、CLIP 图像编码 ──> BPU（libdnn，ctypes）
        OCR、CLIP 文本编码、没有 BPU 模型包的模型 ──> ONNX Runtime CPU（原路径）
```

设计原则：

- **不改 immich 的配置和调用方式**。immich 仍然调用 `libx264`、默认解码器和 `decodeImage()`，
  硬件路径藏在下面。
- **每一处都能回退**。硬件做不了的情况走原来的软件路径，结果正确，只是不加速。
- **ABI 不变**。替换的 `libavcodec.so.61`、`libavfilter.so.10` 与官方镜像导出的符号逐个一致，构建脚本会检查。

RDK X5 的编解码硬件只能通过 `libmultimedia.so`（`hb_mm_mc_*` 接口）使用，`/dev/video*` 是 ISP 节点，
FFmpeg 自带的 `h264_v4l2m2m` 用不了，所以需要补丁。

## 2. 视频：jellyfin-ffmpeg 补丁（`server/patches/0001`）

新增 `h264_x5`、`hevc_x5`、`mjpeg_x5` 编码器和解码器，并让常用入口透明地用上它们。

**编码**：`libx264`/`libx265` 打开时先尝试硬件编码器。

- 有 `-b:v` 时用 CBR，否则把 `crf`/`qp` 当作固定 QP。
- GOP 结构为 I-P-P-P、单参考。Wave521 没有 B 帧。
- 宽高按 8 对齐编码，用 SPS 裁剪还原真实尺寸。竖屏视频缩到 720p 后宽度常是 540、406，不对齐会被硬件拒绝。
- 无损、非 4:2:0、高于 8 bit 时用 x264/x265。x264/x265 的 preset、tune、`-x264-params` 在硬件路径上被忽略。
- 编码器只在打开时判断能不能用硬件，运行中出错不会切回软件。

**解码**：`h264_x5`/`hevc_x5`/`mjpeg_x5` 注册在原生解码器之前，成为默认解码器。

- 10-bit、4:2:2、`lowres`、只解关键帧（`-skip_frame nointra`，immich 生成视频缩略图时这样用）时直接用原生解码器。
- 运行中硬件出错时，从最近的关键帧开始把缓存的包重放给原生解码器，已输出的帧不重复。
- 输入队列按 DPB 大小配置，按 `hb_mm_mc_get_status()` 的队列计数推进，不靠短超时轮询（板上内核 HZ=100，1 ms 超时实际要 10 ms）。
- 输出帧的 pts、时长、色彩范围（full range 输出 yuvj420p）与原生解码器一致，测试样本逐帧 md5 相同。

`X5_CODEC_DISABLE=1` 让以上所有入口都回到软件。

## 3. 照片：x5thumb（`server/x5thumb`、`server/immich`）

immich 生成照片预览时调用 `decodeImage(file, { size: 1440 })`：解码原图、按 EXIF 旋转、缩小到短边 1440，
然后再生成缩略图和 thumbhash。补丁让它先调用 `x5thumb <file> 1440`：

1. **JPEG**：JPU 解码出 NV12。只支持 baseline/extended sequential 8-bit，progressive 返回 3。
2. **HEIC**：iPhone 的 HEIC 是 48 个 512×512 的 HEVC 帧内图块拼成的网格。用**一个** HEVC 解码器（即补丁里的 VPU 解码器）逐块解码再拼接。
   不能直接用 ffmpeg 命令行：它给每个图块开一个解码器，一张图约 100 个，会耗尽内存。
   - 色彩范围从第一个图块用原生解码器读出，因为 VPU 不导出 VUI 的 full range 标志。
   - HEIF 的 `irot` 旋转交给 sharp 在小图上做，带 `imir` 镜像的文件回退。
3. **2D GPU**（nano2D `n2d_filterblit`，bilinear）缩小到目标尺寸，在 CPU 上转成 RGB。
4. 小图编码成 PNG、带上原图的 ICC 配置，送回 immich 原来的 `getImageDecodingPipeline()`，
   色彩管理和 EXIF 方向与官方路径完全相同。

返回 3（不处理）的情况走 sharp：progressive JPEG、非 8-bit、奇数宽高、带镜像的 HEIF、
非 4:2:0 HEIC、原图短边不大于目标尺寸。设 `IMMICH_X5THUMB=`（空）可关闭整个照片硬件路径。

2D GPU 的几个行为与直觉不同，实现里已处理：

| 现象 | 处理 |
|---|---|
| YUV 输入总被当作 limited range，`N2D_CSC_SET_FULL_RANGE` 只影响输出侧 | 送入前把 JPEG 的 full range 压成 limited，GPU 内 BT.601 limited 往返，出来再展开 |
| YUV→YUV 缩放内部也经过 RGB | 用与源一致的矩阵（BT.601/709） |
| `N2D_RGB888` 实际字节序是 B,G,R，且直接输出 RGB 很慢 | GPU 输出 NV12，CPU 转 RGB |
| n2d 分配的缓冲对 CPU 不带 cache，逐像素读很慢 | 先整块拷出再转换 |
| `filterblit` 带旋转返回错误，源缓冲设旋转会段错误 | 旋转交给 sharp（或先缩放再用同尺寸 `n2d_blit` 旋转） |
| 驱动只有一个上下文 | 所有进程用文件锁 `/tmp/x5thumb.n2d.lock` 串行使用 2D GPU |

用户手册写 2D GPU 最大 4K，实测 8000×6000 输入缩小也能完成。

画质差异的主要来源：immich 在线性光（scRGB）里缩放，2D GPU 在 gamma 空间缩放，见 [性能数据](benchmarks.md) 1.3 节。

## 4. 可选：视频缩放和旋转走 2D GPU（`server/patches/0002`）

`scale` 滤镜（同格式、同范围的 8-bit 4:2:0 缩放）和 `transpose` 滤镜（顺/逆时针 90°）可以走 2D GPU，
单个视频 CPU 降到约三分之一。但每帧的 GPU 操作在滤镜线程里串行等待，单个视频变慢 25%～150%，
所以镜像默认 `X5_N2D_FILTERS=0` 关闭。CPU 比时间更紧张时，可在覆盖文件里给 immich-server 设
`X5_N2D_FILTERS=1`。没有 `/dev/nano2d` 时自动使用 swscale。

## 5. 机器学习：BPU 会话（`ml/docker/overlay`）

- 新增 `immich_ml/sessions/bpu/`：用 ctypes 直接调用 `libdnn.so`（hb_dnn），不依赖特定 Python 版本的厂商包。
  每个模型目录里是按静态输入尺寸编译的 `.bin` 和描述输出的 `model.json`，约定与 immich 的 RKNN 后端相同：
  输入为未归一化的 uint8 NHWC 像素，均值、方差和通道顺序编进模型。
- 修改 `models/base.py`：模型目录里有 `bpu/x5/` 模型包时用 BPU 会话，没有就用原 ONNX 会话，按模型逐个回退。
  `MACHINE_LEARNING_BPU=false` 整体退回 CPU。
- 被覆盖的 immich 文件的上游 sha256 记在 `upstream.sha256`，基础镜像变了构建会失败，避免覆盖不匹配的版本。

模型转换（`ml/convert/`，x86 主机 + OE 1.2.8）得到可用精度的三个前提：

1. 按 BPU 实际读取的布局组织输入数据（与 `hb_dnn` 报告的属性不一致，以实测为准）；
2. ArcFace 的 PRelu 改写为基础算子；
3. 校准使用 `max` 方法并开启全 int16。

缺任一项，特征余弦会从 0.99 掉到 0.1～0.5。OCR（PP-OCRv5_mobile）在 BPU 上精度不达标，仍在 CPU 上。

## 6. 评估过但没有采用的硬件

| 单元 | 结论 |
|---|---|
| VSE | 1080p→720p 3.8 ms，最快，但画质比 2D GPU 低（41 dB 对 50 dB，以 lanczos 为参考）；拒绝 12 MP 输入，照片用不上；缩放时不能旋转 |
| GDC | 旋转 90° 是按网格插值重采样，不是无损转置，与精确转置只有约 24 dB；1080p 旋转报错 |
| 3D GPU（OpenCL） | 未测。它可以在线性光里缩放、没有 4K 限制，可能补上照片画质差距 |
| 视频缩略图用硬件 | 只解关键帧在 CPU 上很轻，硬件要逐帧解码反而更慢 |

## 7. 环境变量

| 变量 | 所在容器 | 默认 | 作用 |
|---|---|---|---|
| `X5_CODEC_DISABLE` | server | 未设 | `1`：所有编解码和 2D GPU 滤镜回到软件 |
| `X5_N2D_FILTERS` | server | `0` | `1`：视频 `scale`/`transpose` 走 2D GPU |
| `IMMICH_X5THUMB` | server | `/usr/local/bin/x5thumb` | 设为空：照片预览走 sharp |
| `LIBMM_FM_PATH` | server | `/usr/local/lib/x5/firmware/` | VPU 固件目录 |
| `MACHINE_LEARNING_BPU` | ML | `true` | `false`：ML 全部走 CPU |
