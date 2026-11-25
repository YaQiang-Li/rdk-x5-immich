# 故障排查

[English](../en/troubleshooting.md) | 中文

## 先看什么

```sh
cd /opt/immich
sudo docker compose -f docker-compose.yml -f docker-compose.x5.yml ps
sudo docker logs --since 30m immich_server 2>&1 | grep -iE "error|fail" | tail
sudo cat /sys/kernel/debug/vpu/loading /sys/kernel/debug/jpu/loading
sudo awk '/^ +total +[0-9]/{print $2/1048576 " MB"}' /sys/kernel/debug/ion/heaps/cma_reserved
free -m
```

想对比是不是硬件路径的问题，先关掉它再试：server 设 `X5_CODEC_DISABLE=1`（编解码和 2D GPU 滤镜）
或 `IMMICH_X5THUMB=`（照片预览），ML 设 `MACHINE_LEARNING_BPU=false`。在覆盖文件对应服务下加
`environment:` 后重新 `up -d`。

## 常见问题

**转码日志里没有 `using RDK X5 hardware encoder`，或出现 `No fimware chagall.bin`**

设备或固件没进容器。确认覆盖文件生效（`docker inspect immich_server` 能看到 `/dev/vpu` 等设备和
`/usr/local/lib/x5/firmware` 挂载），并且命令带了两个 `-f`。

**`deploy/check-board.sh` 报 `/dev/bpu` 或 `/dev/nano2d` 不存在**

系统版本或内核模块不对。本方案在 RDK X5 系统 3.5.0 上验证。

**缩略图任务时板子变得很卡，甚至 SSH 连不上**

内存耗尽。降低"生成缩略图"并发（4 GB 板用 1～2），ML 仍有 CPU 模型时按安装指南第 7 节限制线程，
确认有 swap。内存耗尽时内核可能杀掉正在用 VPU 的进程，之后 VPU 可能卡死，连官方示例也无法运行，
只能重启板子。

**某些照片的预览与以前略有不同**

硬件在 gamma 空间缩放，immich 在线性光里缩放，细节特别密的照片差异较明显。设 `IMMICH_X5THUMB=`
后在网页上对该照片"刷新缩略图"，即可用软件重新生成。

**转码后的视频体积或画质与以前不同**

硬件编码没有 B 帧，immich 的 crf 被当作固定 QP。实测 crf 23 时，手机视频的码率多数低于 x264
ultrafast、PSNR 更高；画面几乎静止的视频（如监控画面）码率约为 x264 的三分之一，PSNR 低约 5 dB。
需要 x264 的结果时设 `X5_CODEC_DISABLE=1`。

**ML 日志没有 `Loading BPU model`**

模型包路径不对，或没带覆盖文件启动（覆盖文件把 `MACHINE_LEARNING_MODEL_REVISION` 设为 `main`，见安装指南第 6 节）。
模型包应在 `/cache/<任务>/<模型>/<类型>/bpu/x5/` 下：

```sh
sudo docker exec immich_machine_learning find /cache -path '*bpu*' -type f
```

**模型一直下载失败、ML 接口 500**

国内访问 huggingface.co 不通。`.env` 加 `HF_ENDPOINT=https://hf-mirror.com` 和 `HF_HUB_DISABLE_XET=1`，
然后 `up -d` 重建 ML 容器（改 env 对已有容器不生效）。

**板上在一条命令里用 `&&` 串起 docker 命令时报"必须显式指定用户身份"**

这是 RDK 系统上 docker 包装脚本在交互 shell 里的行为。把命令拆开执行，或给 `docker run`/`docker exec`
加 `-u`；在脚本里调用不受影响。

**`docker compose down` 之后断电，来电后 immich 没起来**

`down` 会删除容器，`restart: always` 只对已存在的容器生效。来电后手动 `up -d`。

**升级 immich 后构建失败**

- `server/build.sh` 报符号不一致或补丁打不上：新版本的 jellyfin-ffmpeg 变了，需要更新补丁。
- `ml/build.sh` 报 `sha256sum: WARNING`：被覆盖的 immich 文件变了，需要更新 overlay。

在补丁更新之前，继续使用 v3.4.2。
