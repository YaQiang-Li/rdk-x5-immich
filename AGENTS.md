# AGENTS.md — rdk-x5-immich

公开仓库：让 immich（v3.4.2）在 RDK X5 上用 VPU/JPU/2D GPU/BPU 加速。面向持有 RDK X5 的用户，
中英双语。只收用户需要的东西，开发过程的原型和记录不进仓库。

## 目录约定

| 路径 | 放什么 |
|---|---|
| `server/` | immich-server 镜像：`patches/`（jellyfin-ffmpeg 补丁）、`x5thumb/`、`immich/`（server 补丁）、`Dockerfile`、`build.sh` |
| `ml/` | immich-machine-learning 镜像：`docker/`（Dockerfile + overlay）、`build.sh`、`convert/`（x86 模型转换）、`tools/`（板端评测） |
| `deploy/` | compose 覆盖文件、部署前检查脚本 |
| `tools/` | 可选的运维/观测脚本 |
| `docs/zh/`、`docs/en/` | 文档，两种语言内容一一对应，改一边必须同步另一边 |
| `LICENSES/` | 许可证全文；各目录的许可见根目录 README 的 License 一节 |

不放：编译产物、模型 .bin、厂商库和固件（构建时从板上 `/usr/hobot` 取）、测试数据、开发过程日志。

## 规则

- 不出现内网地址、内部链接、个人设备 IP、密码、token；示例 IP 用 `<board-ip>`。
- 性能数字必须来自开发记录里的实测日志，文档注明测试条件（板子、样本、并发）。
- 改了构建脚本或补丁，必须在一块 RDK X5 上从仓库副本干净构建并跑端到端验证（见 `docs/zh/install.md` 的验证一节）。
- 代码：tab 缩进（补丁里的 FFmpeg 代码按 FFmpeg 风格 4 空格），行宽 ≤ 80 推荐、≤ 120 必须；
  shell 脚本 POSIX sh，`shellcheck -x -s sh` 全绿（在脚本所在目录运行），LF 换行。
- commit 签名 `yaqiang.li <yaqiang.li@d-robotics.cc>`。
