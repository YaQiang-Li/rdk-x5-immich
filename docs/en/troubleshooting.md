# Troubleshooting

English | [中文](../zh/troubleshooting.md)

## First look

```sh
cd /opt/immich
sudo docker compose -f docker-compose.yml -f docker-compose.x5.yml ps
sudo docker logs --since 30m immich_server 2>&1 | grep -iE "error|fail" | tail
sudo cat /sys/kernel/debug/vpu/loading /sys/kernel/debug/jpu/loading
sudo awk '/^ +total +[0-9]/{print $2/1048576 " MB"}' /sys/kernel/debug/ion/heaps/cma_reserved
free -m
```

To tell whether a problem comes from the hardware path, turn it off and try again: on the server
`X5_CODEC_DISABLE=1` (codecs and 2D GPU filters) or `IMMICH_X5THUMB=` (photo previews), on ML
`MACHINE_LEARNING_BPU=false`. Add `environment:` to the service in the override file and run `up -d`.

## Common problems

**No `using RDK X5 hardware encoder` in the transcoding log, or `No fimware chagall.bin`**

The devices or the firmware are not in the container. Check that the override file is in effect
(`docker inspect immich_server` shows `/dev/vpu` etc. and the `/usr/local/lib/x5/firmware` mount) and that both
`-f` options were given.

**`deploy/check-board.sh` reports `/dev/bpu` or `/dev/nano2d` missing**

Wrong system version or kernel modules. This project is verified on RDK X5 system 3.5.0.

**The board gets very slow during thumbnail jobs, SSH may stop responding**

Out of memory. Lower the "Generate thumbnails" concurrency (1–2 on 4 GB boards), limit the ML threads if some
models still run on the CPU (section 7 of the installation guide), and make sure there is swap. When memory runs
out the kernel may kill a process that is using the VPU; the VPU can then hang (even the vendor samples stop
working) until the board is rebooted.

**Some previews look slightly different than before**

The hardware resizes in gamma space, immich in linear light; photos with very fine detail differ the most. Set
`IMMICH_X5THUMB=` and use "Refresh thumbnails" on that photo to regenerate it in software.

**Transcoded videos differ in size or quality from before**

The hardware encoder has no B-frames and immich's crf is used as a constant QP. Measured at crf 23, phone videos
mostly get a lower bitrate than x264 ultrafast with a higher PSNR; almost static videos (such as surveillance
footage) get about a third of x264's bitrate and about 5 dB lower PSNR. Set `X5_CODEC_DISABLE=1` when x264's
result is needed.

**No `Loading BPU model` in the ML log**

The model packs are in the wrong place, or the stack was started without the override file (it sets
`MACHINE_LEARNING_MODEL_REVISION` to `main`, see section 6 of the installation guide). The packs belong under `/cache/<task>/<model>/<type>/bpu/x5/`:

```sh
sudo docker exec immich_machine_learning find /cache -path '*bpu*' -type f
```

**Model downloads keep failing, ML returns HTTP 500**

huggingface.co is unreachable. Add `HF_ENDPOINT=https://hf-mirror.com` and `HF_HUB_DISABLE_XET=1` to `.env`
and recreate the ML container with `up -d` (changed env does not apply to an existing container).

**Docker commands chained with `&&` on the board fail with "the user identity must be given explicitly"**

That is the docker wrapper of the RDK system in interactive shells. Run the commands one by one or pass `-u` to
`docker run`/`docker exec`; scripts are not affected.

**After `docker compose down` and a power loss, immich does not come back**

`down` removes the containers and `restart: always` only applies to existing ones. Run `up -d` after power returns.

**The build fails after an immich upgrade**

- `server/build.sh` reports a symbol mismatch or a patch does not apply: jellyfin-ffmpeg changed and the patches
  need updating.
- `ml/build.sh` reports `sha256sum: WARNING`: an overridden immich file changed and the overlay needs updating.

Stay on v3.4.2 until the patches are updated.
