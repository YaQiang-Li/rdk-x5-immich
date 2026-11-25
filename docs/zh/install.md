# 安装指南

[English](../en/install.md) | 中文

从一块装好系统的 RDK X5 开始，部署 immich v3.4.2 并启用硬件加速。已经在跑 immich v3.4.2 的话，
直接从第 4 节开始。

## 1. 硬件与系统

| 项 | 要求 |
|---|---|
| 板子 | RDK X5，推荐 **8 GB** 内存版。4 GB 版可用，但缩略图并发要调低（见第 7 节） |
| 系统 | RDK X5 官方系统 3.5.0（`cat /etc/version`），已验证 |
| 系统盘 | eMMC 或 TF 卡上留出 ≥10 GB 给镜像和数据库；自己构建镜像另需约 6 GB |
| 照片盘 | 单独的 SSD/U 盘，容量按"现有照片 × 2"起步（缩略图和转码约再占 10%～20%） |

几点经验：

- **数据库只放系统盘**，不要放 USB 盘。immich 官方文档："the Postgres database (`DB_DATA_LOCATION`)
  should ideally use local SSD storage, and never a network share of any kind"。USB 盘掉线会损坏数据库。
- 照片盘在 `/etc/fstab` 里用 UUID 挂载并加 `nofail`，否则拔盘后系统起不来：
  `UUID=<uuid>  /mnt/photos  ext4  defaults,nofail  0  2`
- 2.5 寸机械盘、3.5 寸机械盘、多盘串在无源 Hub 上都需要独立供电；U 盘和 M.2 SSD 硬盘盒可直接插板载 USB。
- 不装 RTC 电池的话，断电开机后时钟回到 2000 年，HTTPS 会失效。
- 不要 `apt full-upgrade`：可能升级内核导致板载驱动失效。

## 2. 安装 Docker

```sh
curl -fsSL https://get.docker.com | sudo sh
docker compose version        # 必须是 v2 插件；docker-compose(v1) 不受 immich 支持
```

限制容器日志大小，否则日志会吃满系统盘（国内可同时加镜像加速 `registry-mirrors`）：

```sh
sudo tee /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
sudo systemctl restart docker
```

建议在系统盘上建 2 GB swap，扫描大图库时会用到。

## 3. 部署官方 immich v3.4.2

```sh
sudo mkdir -p /opt/immich && cd /opt/immich
V=v3.4.2
sudo wget -O docker-compose.yml \
	https://github.com/immich-app/immich/releases/download/$V/docker-compose.yml
sudo wget -O .env https://github.com/immich-app/immich/releases/download/$V/example.env
```

编辑 `.env`：

```sh
UPLOAD_LOCATION=/mnt/photos/library     # 照片盘
DB_DATA_LOCATION=/opt/immich/postgres   # 系统盘
IMMICH_VERSION=v3.4.2                   # 固定版本，不要用滚动标签 v3/release
TZ=Asia/Shanghai
DB_PASSWORD=<只用字母和数字>
# 国内访问 huggingface.co 不通时（ML 模型下载失败、接口 500）：
HF_ENDPOINT=https://hf-mirror.com
HF_HUB_DISABLE_XET=1
```

官方 `docker-compose.yml` 不用改。

```sh
cd /opt/immich && sudo docker compose up -d
sudo docker compose ps        # 四个容器都应为 healthy
```

浏览器打开 `http://<board-ip>:2283`，第一个注册的账号是管理员。注册后在"管理 → 设置"里关闭公开注册。

## 4. 检查板子

```sh
git clone https://github.com/YaQiang-Li/rdk-x5-immich && cd rdk-x5-immich
sudo sh deploy/check-board.sh
```

它检查 `/dev/vpu`、`/dev/jpu`、`/dev/ion`、`/dev/nano2d`、`/dev/bpu*`、厂商库和 VPU 固件、内存和 ION。
全部 `ok` 再继续。

## 5. 获取镜像和模型

### 5.1 镜像

二选一：

**A. 拉取预构建镜像**

```sh
sudo docker pull ghcr.io/yaqiang-li/immich-server:v3.4.2-x5
sudo docker pull ghcr.io/yaqiang-li/immich-machine-learning:v3.4.2-bpu
```

然后在 `/opt/immich/.env` 里加两行，让覆盖文件用这两个镜像：

```sh
IMMICH_X5_SERVER_IMAGE=ghcr.io/yaqiang-li/immich-server:v3.4.2-x5
IMMICH_X5_ML_IMAGE=ghcr.io/yaqiang-li/immich-machine-learning:v3.4.2-bpu
```

**B. 在板上自己构建**

```sh
sudo apt install -y hobot-multimedia-dev     # 若 /usr/include/hb_media_codec.h 不存在
sudo sh server/build.sh      # 约 30 分钟，需联网下载 jellyfin-ffmpeg 源码和 Debian 依赖
sudo sh ml/build.sh          # 几分钟
```

`server/build.sh` 在官方镜像里重新编译同版本的 jellyfin-ffmpeg，并检查新库导出的符号与官方一致；
国内可设 `APT_MIRROR=https://mirrors.ustc.edu.cn` 加速。生成本地镜像 `immich-server:v3.4.2-x5` 和
`immich-machine-learning:v3.4.2-bpu`，覆盖文件默认就用这两个名字。

### 5.2 BPU 模型包

从 [Releases](https://github.com/YaQiang-Li/rdk-x5-immich/releases) 下载两个模型包，先核对 `SHA256SUMS`，再解压进 ML 的模型缓存卷
（官方 compose 的项目名是 `immich`，所以卷名是 `immich_model-cache`，可用 `docker volume ls` 确认）：

```sh
sha256sum -c SHA256SUMS
for f in bpu-models-buffalo_l-x5.tar.gz bpu-models-clip-vit-b-32-openai-x5.tar.gz; do
	sudo docker run --rm -u 0:0 -v immich_model-cache:/cache -v "$PWD":/m \
		--entrypoint tar ghcr.io/immich-app/immich-machine-learning:v3.4.2 xzf "/m/$f" -C /cache
done
```

解压后的目录：

```
facial-recognition/buffalo_l/detection/bpu/x5/{model.json,batch1_height640_width640.bin}
facial-recognition/buffalo_l/recognition/bpu/x5/{model.json,batch1_height112_width112.bin}
clip/ViT-B-32__openai/visual/bpu/x5/{model.json,batch1_height224_width224.bin}
```

人脸模型由 InsightFace `buffalo_l` 转换而来，仅限非商业用途，见根目录 README 的 License 一节。
想自己从 ONNX 转换的话，见 [`ml/convert/`](../../ml/convert)（需要 D-Robotics OE 1.2.8 工具链和一台 x86 主机）。

没有模型包时，ML 镜像照常工作，只是推理仍在 CPU 上。

## 6. 启用

```sh
sudo cp deploy/docker-compose.x5.yml /opt/immich/
cd /opt/immich
sudo docker compose -f docker-compose.yml -f docker-compose.x5.yml up -d
```

以后所有 `docker compose` 命令都要带上这两个 `-f`。覆盖文件还把 ML 的
`MACHINE_LEARNING_MODEL_REVISION` 设为 `main`：BPU 模型包按不带 revision 的旧目录布局存放，
官方 compose 里的 `v2` 会让 ML 找不到它们（模型输出与 v2 等价，CPU 模型会按旧布局重新下载一次）。

## 7. 推荐设置

在"管理 → 设置 → 任务设置"里：

| 任务 | 8 GB 板 | 4 GB 板 |
|---|---|---|
| 生成缩略图 | 4 | 1～2 |
| 视频转码 | 1（默认） | 1 |

**4 GB 板请在第一次大批量上传之前就把各任务并发都改成 1**，并建好 swap：在 4 GB 板上实测，用 immich
默认并发上传几十张照片时，immich-server 被内核 OOM 杀掉过两次。

照片缩略图变快以后，不需要靠高并发提速；并发过高时 ION（硬件内存）和系统内存都可能耗尽，
内存耗尽时 VPU 可能卡死，只能重启板子恢复。

如果 ML 仍有模型在 CPU 上跑（OCR、CLIP 文本编码），可以在 `.env` 里限制线程，避免整机卡死：

```sh
MACHINE_LEARNING_REQUEST_THREADS=4
MACHINE_LEARNING_MODEL_INTRA_OP_THREADS=4
MACHINE_LEARNING_MODEL_INTER_OP_THREADS=1
```

## 8. 验证

```sh
# 视频编码走 VPU
sudo docker exec immich_server ffmpeg -hide_banner -nostats -f lavfi \
	-i testsrc2=s=1280x720 -frames:v 30 -c:v libx264 -f null - 2>&1 | grep "RDK X5"
# 预期: using RDK X5 hardware encoder ...

# 照片预览走硬件：任选一张图库里的大 JPEG（容器内路径），输出第一行是尺寸
sudo docker exec immich_server sh -c 'x5thumb "<容器内照片路径>" 1440 | head -1'
# 预期: 例如 1920 1440 1920 1440 0；返回码 3 表示这张图走软件（progressive 等）

# BPU 模型加载
sudo docker logs immich_machine_learning 2>&1 | grep "Loading BPU model"
```

然后在网页上传一张照片、一个视频，确认缩略图和播放正常。观察硬件负载：

```sh
sudo cat /sys/kernel/debug/vpu/loading /sys/kernel/debug/jpu/loading   # JPU 单位 0.1%
sudo awk '/^ +total +[0-9]/{print $2/1048576 " MB"}' /sys/kernel/debug/ion/heaps/cma_reserved
```

## 9. 回滚

```sh
cd /opt/immich
sudo docker compose -f docker-compose.yml up -d
```

生成的缩略图、转码文件、人脸和搜索特征与官方格式相同，回滚后无需处理。只回滚其中一个服务时，
可以注释掉覆盖文件里对应的服务段。

## 10. 升级 immich

本方案的镜像只对 v3.4.2 验证过。升级前：

1. 读 immich 的 release note。
2. 用新版本的官方镜像重新构建：`sudo sh server/build.sh ghcr.io/immich-app/immich-server:<新版本>`、
   `sudo sh ml/build.sh ghcr.io/immich-app/immich-machine-learning:<新版本>`。
   - server 的构建在 jellyfin-ffmpeg 版本或 `media.repository.js` 变化时会失败，需要更新补丁。
   - ML 的构建在被覆盖的 immich 文件变化时失败（`upstream.sha256` 校验），需要更新 overlay。
3. 改 `.env` 的 `IMMICH_VERSION` 和覆盖文件里的镜像名，再 `up -d`。

## 11. 备份

[`tools/immich-backup.sh`](../../tools/immich-backup.sh) 每天导出数据库（保留 30 天）并把原图增量
复制到另一块盘，不复制可重新生成的缩略图和转码文件，也不删除备份盘上已在 immich 里删掉的文件：

```sh
sudo LIBRARY=/mnt/photos/library BACKUP_ROOT=/mnt/backup sh tools/immich-backup.sh
# cron: 30 4 * * * LIBRARY=/mnt/photos/library BACKUP_ROOT=/mnt/backup sh /path/immich-backup.sh
```

恢复数据库：

```sh
sudo docker exec -i immich_postgres pg_restore -U postgres -d immich --clean \
	< /mnt/backup/db/immich-<日期>.dump
```
