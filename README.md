# rdk-x5-immich

[English](README.en.md) | 中文

让 [immich](https://immich.app) 自建相册在 **RDK X5** 开发板上用片上硬件干活：
视频编解码交给 VPU，照片解码交给 JPU/VPU、缩放交给 2D GPU，人脸识别和以图搜图交给 BPU。
immich 本身不用改配置，换两个镜像、加一个 compose 覆盖文件即可；硬件处理不了的情况自动回退原来的软件路径。

适配版本：immich **v3.4.2**，RDK X5 系统 3.5.0。

## 效果

均为 RDK X5 实测，测试条件见 [docs/zh/benchmarks.md](docs/zh/benchmarks.md)。

| 任务 | 官方镜像 | 本方案 | 用到的硬件 |
|---|---|---|---|
| 重建 200 张照片的缩略图（8 GB 板，并发 4） | 349 s | **134 s** | JPU、VPU、2D GPU |
| 单张 JPEG 预览解码+缩放的 CPU 时间（≥2 MP） | 5.0 s | **1.2 s** | JPU、2D GPU |
| 单张 HEIC 预览解码+缩放的 CPU 时间 | 7.7～9.9 s | **1.3～1.9 s** | VPU、2D GPU |
| 10 个视频转码（4 GB 板） | 126 s，CPU 70% | **91 s**，CPU 53% | VPU |
| 人脸检测+识别（7 人合影，端到端） | 13.07 s | **0.35 s** | BPU |
| CLIP 图像编码（端到端） | 1.03 s | **0.18 s** | BPU |

输出与官方路径的一致性：视频硬解与软解逐帧一致；照片预览与官方 PSNR 中位数约 39 dB；
人脸特征余弦均值 0.999，CLIP 图像特征余弦均值 0.991。

## 组成

| 镜像 | 改动 | 目录 |
|---|---|---|
| `immich-server:v3.4.2-x5` | 镜像内的 jellyfin-ffmpeg 打补丁：`libx264`/`libx265`/默认解码器透明地走 VPU/JPU；照片预览由 `x5thumb` 用 JPU/VPU + 2D GPU 完成 | [`server/`](server) |
| `immich-machine-learning:v3.4.2-bpu` | 新增 BPU 推理会话，人脸检测、人脸识别、CLIP 图像编码在 BPU 上运行 | [`ml/`](ml) |

BPU 模型包（由 immich 的 ONNX 模型转换）从 [Releases](https://github.com/YaQiang-Li/rdk-x5-immich/releases) 下载。

## 快速开始

前提：已按 immich 官方方式用 docker compose 部署了 v3.4.2（完整步骤见 [安装指南](docs/zh/install.md)）。

```sh
git clone https://github.com/YaQiang-Li/rdk-x5-immich && cd rdk-x5-immich
sudo sh deploy/check-board.sh                       # 检查设备节点、厂商库、内存
# 镜像：直接拉取预构建镜像，或在板上自己构建（server/build.sh、ml/build.sh）
docker pull ghcr.io/yaqiang-li/immich-server:v3.4.2-x5
docker pull ghcr.io/yaqiang-li/immich-machine-learning:v3.4.2-bpu
# BPU 模型包解压进 ML 的模型缓存卷，见安装指南第 5 节
cp deploy/docker-compose.x5.yml /opt/immich/
cd /opt/immich
docker compose -f docker-compose.yml -f docker-compose.x5.yml up -d
```

回滚：去掉 `-f docker-compose.x5.yml` 再 `up -d`。

## 文档

- [安装指南](docs/zh/install.md)：从零部署 immich、启用硬件加速、验证、回滚、升级、备份
- [设计说明](docs/zh/design.md)：各硬件怎么接进 immich，回退规则，踩过的坑
- [性能数据](docs/zh/benchmarks.md)
- [故障排查](docs/zh/troubleshooting.md)

## 限制

- 只针对 immich v3.4.2 验证过。升级 immich 时要用新版本的官方镜像重新构建（构建脚本会校验 ABI 和被覆盖文件的 sha256，不匹配时构建失败）。
- 4 GB 内存的板子要把缩略图并发调低；内存耗尽可能让 VPU 卡死，只能重启恢复。
- 硬件编码没有 B 帧，immich 的 `crf` 被当作固定 QP。
- progressive JPEG、10-bit HEVC 等走软件。OCR 仍在 CPU 上。BPU 只转换了默认的 `buffalo_l` 和 `ViT-B-32__openai`。

## License

| 部分 | 许可 |
|---|---|
| `server/patches/`（jellyfin-ffmpeg 补丁） | LGPL-2.1-or-later，与 FFmpeg 一致 |
| `server/immich/`、`ml/docker/overlay/`（含 immich 代码） | AGPL-3.0，与 immich 一致 |
| 其余代码和文档 | MIT |

许可证全文在 [`LICENSES/`](LICENSES)。

预构建镜像包含 FFmpeg（GPL 构建）、immich（AGPL-3.0）以及 D-Robotics 的 RDK X5 运行库和 VPU 固件，各自遵循原许可。

**BPU 模型包**：人脸模型由 InsightFace 的 `buffalo_l` 转换而来，InsightFace 的预训练模型仅限
**非商业研究用途**，使用前请阅读 [InsightFace 的许可说明](https://github.com/deepinsight/insightface#license)；
CLIP 模型来自 OpenAI CLIP（MIT）。
