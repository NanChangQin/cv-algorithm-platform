# 本地服务器部署说明

目标环境：Linux、Docker Engine 26+、Docker Compose v2；GPU 部署需 NVIDIA 驱动、`nvidia-smi` 与 NVIDIA Container Toolkit。生产运行应叠加 `compose.database.yaml`，由 PostgreSQL 保存平台业务数据。

## 1. 部署前检查

```powershell
docker version
docker compose version
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.6.2-base-ubuntu22.04 nvidia-smi
```

CPU-only 节点可跳过最后两项，但 GPU manifest 的算法不能在该节点运行。

## 2. 宿主机目录

```text
/srv/cv-platform/
├── data/
│   ├── assets/                # 图片
│   ├── videos/                # 上传视频
│   └── media-frames/          # 视频/流抽帧
├── packages/                  # 算法 ZIP 与受控构建上下文
└── models/
    ├── faster-rcnn-resnet50/
    │   ├── model.pth
    │   └── model-config.yaml
    └── yolo-detector/
        └── model.pt
```

创建目录并确保 Docker daemon 与 Compose 容器可读写。PowerShell 7 可运行：

```powershell
./scripts/prepare-server.ps1
```

## 3. 环境变量

复制 `.env.example` 为 `.env`，必须修改管理员密码：

```text
CV_PLATFORM_ADMIN_USERNAME=admin
CV_PLATFORM_ADMIN_PASSWORD=<至少 12 位强密码>
CV_PLATFORM_AUTH_ENABLED=false
CV_PLATFORM_HTTP_PORT=8080
CV_PLATFORM_DATA_ROOT=/srv/cv-platform/data
CV_PLATFORM_MODEL_ROOT=/srv/cv-platform/models
CV_PLATFORM_PACKAGE_ROOT=/srv/cv-platform/packages
CV_PLATFORM_SECURE_COOKIES=false
```

多服务器部署或 Manager 端口不只在本机可达时，还需设置 `CV_PLATFORM_MANAGER_API_TOKEN`，并在每个 GPU 节点配置相同的值，见 [GPU 节点访问令牌](GPU-NODES.md#访问令牌节点端口对局域网开放时必须配置)。

`CV_PLATFORM_AUTH_ENABLED=false` 时前端隐藏登录和退出入口，Backend 直接以内置管理员身份处理请求，适合隔离的 GPU 测试服务器。若服务器可能被不受信任的用户访问，必须设置 `CV_PLATFORM_AUTH_ENABLED=true`；若前面有 HTTPS 反向代理，同时将 `CV_PLATFORM_SECURE_COOKIES=true`。不要提交 `.env`。

## 4. 验证代码与配置

```powershell
./scripts/verify.ps1
docker compose config --quiet
```

`verify.ps1` 检查 Python 语法、TOML/YAML、架构边界、数据库适配边界和 Manager 多副本自检；已安装依赖时还会运行 pytest 与前端构建。

## 5. 构建与启动

YOLO 训练和推理镜像都基于 PyTorch 2.10.0、CUDA 12.8，Dockerfile 会安装 Ultralytics 8.4.183。可以先拉取本机缺少的基础镜像：

```powershell
docker pull pytorch/pytorch:2.10.0-cuda12.8-cudnn9-runtime
docker pull python:3.12-slim
docker pull node:22-alpine
docker pull nginx:1.27-alpine
```

构建前，按 [Faster R-CNN 权重说明](../runtimes/faster-rcnn-trainer/assets/README.md) 将官方 COCO V1 权重放到 `runtimes/faster-rcnn-trainer/assets/fasterrcnn_resnet50_fpn.pt`。已有同一权重可直接复用，构建时会校验 SHA256 前缀 `258fb6c6`。

按下面的顺序执行，每一步成功后再执行下一步。Faster R-CNN 训练镜像依赖 YOLO 训练镜像，Faster R-CNN 推理镜像又依赖其训练镜像。

```powershell
docker compose --profile algorithm-images build yolo-trainer yolo
docker compose --profile algorithm-images build faster-rcnn-trainer
docker compose --profile algorithm-images build faster-rcnn
docker compose -f compose.yaml -f compose.database.yaml --profile database up -d --build
docker compose -f compose.yaml -f compose.database.yaml --profile database ps
docker compose -f compose.yaml -f compose.database.yaml --profile database logs --tail 200 backend postgres algorithm-manager media-worker
```

访问 `http://<服务器IP>:<CV_PLATFORM_HTTP_PORT>`。默认端口为 `8080`；端口已被其他服务占用时可改为 `8081`。免登录模式会直接进入工作台；启用认证后使用 `.env` 中的管理员账号登录。

## 6. 摄像头、视频与 RTSP

- 视频通过高级编排页面上传，保存到受控 `data/videos`。
- RTSP 使用 `rtsp://` 或 `rtsps://` 地址；确保 media-worker 能访问摄像头网络。
- 默认 Compose 不映射宿主摄像头。Linux 摄像头使用 `docker compose -f compose.yaml -f compose.camera.yaml up -d`；默认 `/dev/video0`，可设置 `CV_PLATFORM_CAMERA_DEVICE=/dev/video1`。

## 7. 多 GPU 与多服务器

每台算法节点部署一个 Algorithm Manager，并挂载本机 Docker Socket、数据和模型目录。主平台的“高级编排 → 运行节点”注册：

```text
node-a  http://10.0.0.11:8010/api/v1
node-b  http://10.0.0.12:8010/api/v1
```

Manager 端口只应对平台内网开放。调度器按节点/GPU 空闲显存选择具体 GPU 索引；算法容器只看到所选 GPU。所有节点需能解析/拉取相同算法镜像，并拥有一致的数据与模型路径（共享存储或同步目录）。

## 8. 网络与隔离

- 前端、Backend、Media Worker、Manager 位于控制面网络。
- 算法运行容器位于 internal network，默认无公网。
- 仅 Manager 挂载 Docker Socket；浏览器和 Backend 不接触 Socket。
- 算法容器固定 UID 10001、只读根、非特权、cap drop、no-new-privileges、CPU/内存/PID/GPU 限制。
- 生产环境建议给 Docker Socket 增加授权代理，并用防火墙限制 Manager API 来源。

## 9. 上线验收

1. 登录、创建用户、项目和 viewer/editor 成员，确认跨项目资源不可见、viewer 不能写入。
2. 上传 JPG/PNG 和 MP4，确认非法 MIME、扩展名及超限文件被拒绝。
3. 启动 Faster R-CNN/YOLO，确认 `/health`、`/metadata`、`/predict` 和统一结果。
4. 同图多算法并排/叠加，下载 JSON、PNG 和结果 ZIP。
5. 运行视频/RTSP 抽帧任务和串并行 DAG。
6. 查看具体 GPU 索引、容器日志、审计日志；模拟容器健康失败。
7. 配置灰度权重总和 100，配置扩缩容并观察副本数及 idle 冷却。
8. 重启 Manager，确认带标签的算法容器重新发现。

## 10. PostgreSQL 建库与版本迁移

项目已经包含类似 Flyway 的 Alembic 版本迁移。先在 `.env` 设置：

```text
CV_PLATFORM_POSTGRES_DB=cv_platform
CV_PLATFORM_POSTGRES_USER=cv_platform
CV_PLATFORM_POSTGRES_PASSWORD=<强随机密码>
```

首次启动 PostgreSQL 并升级到最新结构：

```powershell
docker compose -f compose.yaml -f compose.database.yaml --profile database up -d postgres
docker compose -f compose.yaml -f compose.database.yaml --profile database run --rm database-migrator upgrade head
docker compose -f compose.yaml -f compose.database.yaml --profile database run --rm database-migrator current
```

数据库迁移会创建 `alembic_version` 版本表以及 23 张平台业务表。迁移支持回滚，但生产回滚前必须先完成一致性备份。完整操作见 [数据库迁移说明](../database/README.md)。

数据库组合会为 Backend 设置 `CV_PLATFORM_PERSISTENCE_BACKEND=postgres`。系统健康接口中的 `services.database` 应返回 `ok`；返回 `unavailable` 时 Backend 会以降级状态报告，不应继续上线。

## 11. 恢复与备份

当前需备份：

- `.env` 的安全副本；
- `/srv/cv-platform/data`、`packages`、`models`；
- 算法包和 manifest 是重建镜像的主来源。

业务元数据、登录会话和构建/推理排队状态保存在 PostgreSQL。API 重启不会影响任务，独立 Worker 重启后会重新领取中断的构建与推理。媒体和工作流协调运行中断后仍会明确标记并允许重试。Manager 可通过 Docker 标签恢复算法实例索引。

使用 `scripts/backup-server.sh` 同时备份 PostgreSQL、`data`、`packages` 和 `models`，并用 `scripts/restore-server.sh` 校验和恢复同一恢复点。详细步骤见 `docs/BACKUP-RESTORE.md`。

## 12. BentoML 与 KServe

算法详情可导出：

- BentoML：`bento_service.py`、`bentofile.yaml`、构建命令；
- KServe：安全 `InferenceService`、资源与 HPA 配置、部署命令；
- Docker Compose：单算法容器定义。

生成动作不自动连接外部 BentoCloud/Kubernetes，管理员审查文件后再在目标环境执行。

## 13. Ubuntu 本机部署（手动启动）

本机需要保持 Docker 和平台不随开机启动时，使用 `compose.local.yaml` 覆盖全部平台服务的重启策略为 `no`。
该配置也设置 `ALGORITHM_MANAGER_CONTAINER_RESTART_POLICY=no`，使后续创建的算法推理容器采用相同策略。

第一次安装 Docker Engine、Compose 和 NVIDIA Container Toolkit：

```bash
sudo bash scripts/install-local-runtime.sh "$USER"
```

该脚本使用 Docker 和 NVIDIA 官方软件源，保留已安装的显卡驱动，将指定用户加入 Docker 管理组（具有管理宿主机容器的权限），创建 `/srv/cv-platform/{data,models,packages}`。它会禁用 `docker.service`、`docker.socket` 和 `containerd.service` 的开机启动，只在本次安装结束时手动启动 Docker。安装失败时服务可能仍处于 masked 状态，修复错误后重新运行脚本。

准备 `.env`，设置独立的管理员、数据库密码及 Manager 令牌；仅本机访问时设置 `CV_PLATFORM_HTTP_PORT=127.0.0.1:8080`。可加入以下两行，让直接使用 `docker compose` 时也加载手动启动配置：

```dotenv
COMPOSE_FILE=compose.yaml:compose.database.yaml:compose.local.yaml
COMPOSE_PROFILES=database
```

已具备 Docker 组权限时：

```bash
bash scripts/platform-local.sh config-check
bash scripts/platform-local.sh up
bash scripts/platform-local.sh build-models
bash scripts/platform-local.sh status
```

新加入 Docker 组后，可重新登录，或在当前登录期间使用 `sg docker -c 'bash scripts/platform-local.sh up'`。平台访问地址为 `http://localhost:8080`。

本机代理位于 `127.0.0.1:7897` 时，可执行 `sudo bash scripts/configure-local-docker-proxy.sh`，让 Docker 后台服务通过该代理下载镜像。在 `.env` 中设置 `CV_PLATFORM_BUILD_PROXY=http://127.0.0.1:7897`，让镜像构建步骤使用同一代理。`compose.local.yaml` 使用宿主网络进行构建，启动脚本会明确授予构建所需的 `network.host` 权限；运行中的平台和算法容器仍使用原有的 Compose 网络。

`up` 只在平台镜像缺失时构建。代码更新后先执行 `bash scripts/platform-local.sh build`，再执行 `up`；训练或推理镜像更新使用 `build-models`。

电脑重启后按需启动：

```bash
sudo systemctl start docker
bash scripts/platform-local.sh up
```

停止平台使用 `bash scripts/platform-local.sh stop`；移除平台容器但保留数据库卷使用 `bash scripts/platform-local.sh down`。查看日志使用 `bash scripts/platform-local.sh logs`。如需连 Docker 一起停止，在停止平台后执行 `sudo systemctl stop docker.service docker.socket containerd.service`。
