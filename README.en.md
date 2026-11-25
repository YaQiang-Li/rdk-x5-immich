# rdk-x5-immich

English | [中文](README.md)

Run a self-hosted [immich](https://immich.app) photo library on the **RDK X5** board with its on-chip
hardware doing the heavy lifting: video encoding and decoding on the VPU, photo decoding on the
JPU/VPU, resizing on the 2D GPU, face recognition and image search on the BPU.
immich itself needs no configuration changes: swap two images and add one compose override file.
Whatever the hardware cannot handle falls back to the original software path.

Supported: immich **v3.4.2**, RDK X5 system 3.5.0.

## Results

All measured on RDK X5 boards; test conditions in [docs/en/benchmarks.md](docs/en/benchmarks.md).

| Task | Official images | This project | Hardware |
|---|---|---|---|
| Regenerate thumbnails of 200 photos (8 GB board, concurrency 4) | 349 s | **134 s** | JPU, VPU, 2D GPU |
| CPU time per JPEG preview, decode + resize (≥2 MP) | 5.0 s | **1.2 s** | JPU, 2D GPU |
| CPU time per HEIC preview, decode + resize | 7.7–9.9 s | **1.3–1.9 s** | VPU, 2D GPU |
| Transcode 10 videos (4 GB board) | 126 s, CPU 70% | **91 s**, CPU 53% | VPU |
| Face detection + recognition (group photo of 7, end to end) | 13.07 s | **0.35 s** | BPU |
| CLIP image encoding (end to end) | 1.03 s | **0.18 s** | BPU |

Agreement with the official path: hardware video decoding is frame-exact with software decoding;
photo previews differ by a median PSNR of about 39 dB; face embeddings have a mean cosine similarity
of 0.999 and CLIP image embeddings 0.991.

## What is in it

| Image | Change | Directory |
|---|---|---|
| `immich-server:v3.4.2-x5` | The jellyfin-ffmpeg inside the image is patched: `libx264`/`libx265`/the default decoders use the VPU/JPU transparently; photo previews are made by `x5thumb` with the JPU/VPU and the 2D GPU | [`server/`](server) |
| `immich-machine-learning:v3.4.2-bpu` | A BPU inference session: face detection, face recognition and CLIP image encoding run on the BPU | [`ml/`](ml) |

The BPU model packs (converted from immich's ONNX models) are in the [Releases](https://github.com/YaQiang-Li/rdk-x5-immich/releases).

## Quick start

Prerequisite: immich v3.4.2 deployed with docker compose the official way (full steps in the
[installation guide](docs/en/install.md)).

```sh
git clone https://github.com/YaQiang-Li/rdk-x5-immich && cd rdk-x5-immich
sudo sh deploy/check-board.sh                       # device nodes, vendor libraries, memory
# images: pull the prebuilt ones or build them on the board (server/build.sh, ml/build.sh)
docker pull ghcr.io/yaqiang-li/immich-server:v3.4.2-x5
docker pull ghcr.io/yaqiang-li/immich-machine-learning:v3.4.2-bpu
# unpack the BPU model packs into ML's model cache volume, see section 5 of the guide
cp deploy/docker-compose.x5.yml /opt/immich/
cd /opt/immich
docker compose -f docker-compose.yml -f docker-compose.x5.yml up -d
```

Roll back: drop `-f docker-compose.x5.yml` and run `up -d` again.

## Documentation

- [Installation guide](docs/en/install.md): deploy immich from scratch, enable the hardware, verify, roll back, upgrade, back up
- [Design](docs/en/design.md): how each hardware block is wired into immich, fallback rules, pitfalls
- [Benchmarks](docs/en/benchmarks.md)
- [Troubleshooting](docs/en/troubleshooting.md)

## Limitations

- Verified with immich v3.4.2 only. When upgrading immich, rebuild from the new official images (the build
  scripts check the ABI and the sha256 of the files they replace, and fail on a mismatch).
- On 4 GB boards keep the thumbnail concurrency low; running out of memory can hang the VPU until a reboot.
- The hardware encoder has no B-frames, and immich's `crf` is used as a constant QP.
- Progressive JPEG, 10-bit HEVC and similar stay in software. OCR still runs on the CPU. Only the default
  `buffalo_l` and `ViT-B-32__openai` models were converted for the BPU.

## License

| Part | License |
|---|---|
| `server/patches/` (jellyfin-ffmpeg patches) | LGPL-2.1-or-later, as FFmpeg |
| `server/immich/`, `ml/docker/overlay/` (contain immich code) | AGPL-3.0, as immich |
| Everything else (code and documentation) | MIT |

Full license texts are in [`LICENSES/`](LICENSES).

The prebuilt images contain FFmpeg (GPL build), immich (AGPL-3.0) and D-Robotics' RDK X5 runtime
libraries and VPU firmware, each under its own license.

**BPU model packs**: the face models are converted from InsightFace's `buffalo_l`. InsightFace's
pretrained models are for **non-commercial research use only**; read
[InsightFace's license](https://github.com/deepinsight/insightface#license) before using them.
The CLIP model comes from OpenAI CLIP (MIT).
