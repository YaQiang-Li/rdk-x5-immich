# Installation guide

English | [中文](../zh/install.md)

Starting from an RDK X5 with its system installed, deploy immich v3.4.2 and enable the hardware
acceleration. If immich v3.4.2 is already running, start at section 4.

## 1. Hardware and system

| Item | Requirement |
|---|---|
| Board | RDK X5, the **8 GB** version recommended. 4 GB works with a lower thumbnail concurrency (section 7) |
| System | Official RDK X5 system 3.5.0 (`cat /etc/version`), verified |
| System disk | ≥10 GB free on eMMC or TF card for images and the database; about 6 GB more to build the images yourself |
| Photo disk | A separate SSD/USB drive, sized at "current photos × 2" (thumbnails and transcodes add about 10–20%) |

Lessons learned:

- **Keep the database on the system disk**, not on a USB drive. From immich's documentation: "the Postgres
  database (`DB_DATA_LOCATION`) should ideally use local SSD storage, and never a network share of any kind".
  A USB drive dropping out corrupts the database.
- Mount the photo disk by UUID with `nofail` in `/etc/fstab`, or the system will not boot without it:
  `UUID=<uuid>  /mnt/photos  ext4  defaults,nofail  0  2`
- 2.5" and 3.5" hard disks and several disks on an unpowered hub need their own power supply; USB sticks and
  M.2 SSD enclosures can go straight into the onboard USB ports.
- Without an RTC battery the clock falls back to 2000 after a power loss and HTTPS breaks.
- Do not `apt full-upgrade`: it may upgrade the kernel and break the board's drivers.

## 2. Docker

```sh
curl -fsSL https://get.docker.com | sudo sh
docker compose version        # must be the v2 plugin; docker-compose (v1) is not supported by immich
```

Limit the container logs, or they fill the system disk (add `registry-mirrors` here if needed):

```sh
sudo tee /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
sudo systemctl restart docker
```

A 2 GB swap file on the system disk helps while scanning large libraries.

## 3. Official immich v3.4.2

```sh
sudo mkdir -p /opt/immich && cd /opt/immich
V=v3.4.2
sudo wget -O docker-compose.yml \
	https://github.com/immich-app/immich/releases/download/$V/docker-compose.yml
sudo wget -O .env https://github.com/immich-app/immich/releases/download/$V/example.env
```

Edit `.env`:

```sh
UPLOAD_LOCATION=/mnt/photos/library     # photo disk
DB_DATA_LOCATION=/opt/immich/postgres   # system disk
IMMICH_VERSION=v3.4.2                   # pin it, not the rolling v3/release tags
TZ=Asia/Shanghai
DB_PASSWORD=<letters and digits only>
# where huggingface.co is unreachable (ML model downloads fail, HTTP 500):
HF_ENDPOINT=https://hf-mirror.com
HF_HUB_DISABLE_XET=1
```

The official `docker-compose.yml` stays unchanged.

```sh
cd /opt/immich && sudo docker compose up -d
sudo docker compose ps        # all four containers healthy
```

Open `http://<board-ip>:2283`; the first account registered is the administrator. Then turn off public
sign-up in Administration → Settings.

## 4. Check the board

```sh
git clone https://github.com/YaQiang-Li/rdk-x5-immich && cd rdk-x5-immich
sudo sh deploy/check-board.sh
```

It checks `/dev/vpu`, `/dev/jpu`, `/dev/ion`, `/dev/nano2d`, `/dev/bpu*`, the vendor libraries and VPU
firmware, memory and ION. Continue when everything is `ok`.

## 5. Images and models

### 5.1 Images

Either:

**A. Pull the prebuilt images**

```sh
sudo docker pull ghcr.io/yaqiang-li/immich-server:v3.4.2-x5
sudo docker pull ghcr.io/yaqiang-li/immich-machine-learning:v3.4.2-bpu
```

and add two lines to `/opt/immich/.env` so that the override file uses them:

```sh
IMMICH_X5_SERVER_IMAGE=ghcr.io/yaqiang-li/immich-server:v3.4.2-x5
IMMICH_X5_ML_IMAGE=ghcr.io/yaqiang-li/immich-machine-learning:v3.4.2-bpu
```

**B. Build them on the board**

```sh
sudo apt install -y hobot-multimedia-dev     # if /usr/include/hb_media_codec.h is missing
sudo sh server/build.sh      # about 30 minutes; downloads the jellyfin-ffmpeg source and Debian packages
sudo sh ml/build.sh          # a few minutes
```

`server/build.sh` rebuilds the same jellyfin-ffmpeg version inside the official image and checks that the new
libraries export exactly the official symbols; `APT_MIRROR=<url>` selects a Debian mirror. The result is the
local images `immich-server:v3.4.2-x5` and `immich-machine-learning:v3.4.2-bpu`, the default names in the
override file.

### 5.2 BPU model packs

Download the two model packs from the [Releases](https://github.com/YaQiang-Li/rdk-x5-immich/releases), check `SHA256SUMS`, then unpack them into
ML's model cache volume (the official compose file names the project `immich`, so the volume is `immich_model-cache`; check with
`docker volume ls`):

```sh
sha256sum -c SHA256SUMS
for f in bpu-models-buffalo_l-x5.tar.gz bpu-models-clip-vit-b-32-openai-x5.tar.gz; do
	sudo docker run --rm -u 0:0 -v immich_model-cache:/cache -v "$PWD":/m \
		--entrypoint tar ghcr.io/immich-app/immich-machine-learning:v3.4.2 xzf "/m/$f" -C /cache
done
```

Resulting layout:

```
facial-recognition/buffalo_l/detection/bpu/x5/{model.json,batch1_height640_width640.bin}
facial-recognition/buffalo_l/recognition/bpu/x5/{model.json,batch1_height112_width112.bin}
clip/ViT-B-32__openai/visual/bpu/x5/{model.json,batch1_height224_width224.bin}
```

The face models are converted from InsightFace `buffalo_l` and are for non-commercial use only; see the
License section of the top-level README. To convert from ONNX yourself, see [`ml/convert/`](../../ml/convert)
(needs the D-Robotics OE 1.2.8 toolchain and an x86 host).

Without the model packs the ML image works as usual, with inference on the CPU.

## 6. Enable

```sh
sudo cp deploy/docker-compose.x5.yml /opt/immich/
cd /opt/immich
sudo docker compose -f docker-compose.yml -f docker-compose.x5.yml up -d
```

From now on pass both `-f` options to every `docker compose` command. The override also sets ML's
`MACHINE_LEARNING_MODEL_REVISION` to `main`: the BPU model packs use the old layout without a revision
directory, and the official `v2` would hide them (the model outputs are equivalent; CPU models are downloaded
once more in the old layout).

## 7. Recommended settings

In Administration → Settings → Job settings:

| Job | 8 GB board | 4 GB board |
|---|---|---|
| Generate thumbnails | 4 | 1–2 |
| Video transcoding | 1 (default) | 1 |

**On 4 GB boards set every job concurrency to 1 before the first bulk upload**, and add swap: on a 4 GB board,
uploading a few dozen photos with immich's default concurrencies got immich-server killed by the kernel OOM
killer twice.

With faster photo thumbnails a high concurrency is not needed; too high a concurrency can exhaust ION
(hardware memory) and system memory, and exhausted memory can hang the VPU until a reboot.

If some ML models still run on the CPU (OCR, CLIP text encoding), limit their threads in `.env` so that the
board stays responsive:

```sh
MACHINE_LEARNING_REQUEST_THREADS=4
MACHINE_LEARNING_MODEL_INTRA_OP_THREADS=4
MACHINE_LEARNING_MODEL_INTER_OP_THREADS=1
```

## 8. Verify

```sh
# video encoding on the VPU
sudo docker exec immich_server ffmpeg -hide_banner -nostats -f lavfi \
	-i testsrc2=s=1280x720 -frames:v 30 -c:v libx264 -f null - 2>&1 | grep "RDK X5"
# expected: using RDK X5 hardware encoder ...

# photo previews in hardware: pick a large JPEG from the library (path inside the container);
# the first output line holds the sizes
sudo docker exec immich_server sh -c 'x5thumb "<photo path in the container>" 1440 | head -1'
# expected e.g.: 1920 1440 1920 1440 0; exit code 3 means this photo uses software (progressive etc.)

# BPU models loaded
sudo docker logs immich_machine_learning 2>&1 | grep "Loading BPU model"
```

Then upload a photo and a video in the web UI and check the thumbnails and playback. Hardware load:

```sh
sudo cat /sys/kernel/debug/vpu/loading /sys/kernel/debug/jpu/loading   # JPU in 0.1% units
sudo awk '/^ +total +[0-9]/{print $2/1048576 " MB"}' /sys/kernel/debug/ion/heaps/cma_reserved
```

## 9. Roll back

```sh
cd /opt/immich
sudo docker compose -f docker-compose.yml up -d
```

Thumbnails, transcodes, faces and search embeddings use the official formats, so nothing needs cleaning up.
To roll back only one service, comment out its section in the override file.

## 10. Upgrading immich

The images are verified with v3.4.2 only. Before upgrading:

1. Read immich's release notes.
2. Rebuild from the new official images: `sudo sh server/build.sh ghcr.io/immich-app/immich-server:<new>`,
   `sudo sh ml/build.sh ghcr.io/immich-app/immich-machine-learning:<new>`.
   - The server build fails when the jellyfin-ffmpeg version or `media.repository.js` changed; the patches
     then need updating.
   - The ML build fails when an overridden immich file changed (`upstream.sha256` check); the overlay then
     needs updating.
3. Update `IMMICH_VERSION` in `.env` and the image names, then `up -d`.

## 11. Backup

[`tools/immich-backup.sh`](../../tools/immich-backup.sh) dumps the database daily (kept 30 days) and copies
the originals incrementally to another disk. It skips the regenerable thumbnails and transcodes, and keeps
files on the backup disk that were deleted in immich:

```sh
sudo LIBRARY=/mnt/photos/library BACKUP_ROOT=/mnt/backup sh tools/immich-backup.sh
# cron: 30 4 * * * LIBRARY=/mnt/photos/library BACKUP_ROOT=/mnt/backup sh /path/immich-backup.sh
```

Restore the database:

```sh
sudo docker exec -i immich_postgres pg_restore -U postgres -d immich --clean \
	< /mnt/backup/db/immich-<date>.dump
```
