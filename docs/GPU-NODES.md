# 局域网 GPU 执行节点

主服务器保留前端、API、Worker 和 PostgreSQL。每台 GPU 服务器只需 Docker、NVIDIA 驱动、NVIDIA Container Toolkit 和 Algorithm Manager，无需再部署整个平台。

## 新节点准备

Ubuntu/Debian 已安装驱动、Docker、curl 和 gpg 时，运行：

```bash
sudo bash scripts/install-node-gpu.sh
```

脚本备份 Docker/NVIDIA 配置，安装缺少的 Container Toolkit，再热重载 Docker 运行时配置，不重启现有容器。参考 [NVIDIA 安装说明](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) 和 [Docker 配置热重载](https://docs.docker.com/reference/cli/dockerd/#configuration-reload-behavior)。

将已有的 Manager、算法镜像和 GPU 检测镜像通过 `docker save` / `docker load` 传到新节点：

```bash
docker save -o cv-node-images.tar cv-platform/algorithm-manager:dev cv-platform/faster-rcnn:dev nvidia/cuda:12.6.2-base-ubuntu22.04
# 把 tar 传到 GPU 节点后执行
docker load -i cv-node-images.tar
```

算法镜像须与该节点的 GPU 架构兼容。例如 RTX 50 系列需要支持 Blackwell 的 PyTorch/CUDA 构建；原有 PyTorch 2.1 / CUDA 12.1 镜像不适用。使用 [PyTorch 官方版本对应表](https://pytorch.org/get-started/previous-versions/) 选择含 CUDA 12.8 或更新支持的构建。允许同一个镜像标签在不同节点提供适合各节点硬件的等价算法运行环境。

复制算法权重与模型配置到 `<节点目录>/models/<algorithm_key>/`。新节点没有的运行依赖可以在该节点下载；已存在的镜像和模型优先走局域网。

```bash
export CV_NODE_ROOT=/home/dell/docker/cv-platform-node
export CV_NODE_BIND_IP=192.168.8.135
mkdir -p "$CV_NODE_ROOT"/{data,models,packages}
docker compose -f compose.gpu-node.yaml up -d --pull never
docker compose -f compose.gpu-node.yaml ps
curl http://192.168.8.135:8010/api/v1/system/gpus
```

独立节点模板使用 NVIDIA runtime 的 `NVIDIA_VISIBLE_DEVICES` 绑定 GPU，支持安装 Container Toolkit 后仅热重载 Docker 的接入方式。已有主平台保持原来的 DeviceRequest 模式。

如果节点绑定具体局域网 IP，开机时 Docker 可能先于 Wi-Fi 获取地址而启动，导致端口绑定失败。可安装带网络就绪依赖和失败重试的 systemd 单元（以下用户为 `dell`，默认目录为 `/home/dell/docker/cv-platform-node`）：

```bash
sudo install -m 0644 infra/systemd/cv-platform-gpu-node@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cv-platform-gpu-node@dell.service
systemctl status cv-platform-gpu-node@dell.service
```

单元使用用户自己的 Docker CLI 插件目录，启动时重建 Manager 并等待其健康，恢复开机绑定失败后可能丢失的端口映射，不修改端口绑定范围。算法容器不属于此 Compose 服务，不会被一并重建。自定义部署目录需相应修改 `WorkingDirectory`。

如果 GPU 节点已有兼容的 Conda 环境，可直接复制环境构建算法镜像，仅下载缺少的 Web 接口依赖，原环境不修改：

```bash
docker build --pull=false --build-context gpu-env=/path/to/conda/env \
  -f examples/faster-rcnn/Dockerfile.existing-env -t cv-platform/faster-rcnn:dev .
```

没有现成环境时使用 `examples/faster-rcnn/Dockerfile.gpu-node` 下载所需 PyTorch/CUDA 依赖。带命名构建上下文的方式需要 Buildx；可直接从同架构的主服务器复制 Docker CLI 插件到节点的 `~/.docker/cli-plugins/`。

Manager 端口供受信任局域网主服务器访问。节点 Docker socket 只挂载到本机 Manager。

节点的 `models/` 目录同时挂载进 Manager（`/models`），平台据此检查和复制权重；训练任务的数据集和输出放在 `data/training/`。训练和定向部署见 [TRAINING.md](TRAINING.md)、[DEPLOYMENT.md](DEPLOYMENT.md)。

## 访问令牌（节点端口对局域网开放时必须配置）

Manager 能启停算法容器、读取容器日志。未设置令牌时，能访问节点端口的任何主机都可以直接调用这些接口。为主平台和所有节点配置同一个令牌：

```bash
openssl rand -hex 32   # 生成令牌
```

1. 先在主平台 `.env` 加 `CV_PLATFORM_MANAGER_API_TOKEN=<令牌>`，重建 Backend、Worker 和本机 Manager。未设置令牌的节点会忽略请求头，所以这一步不影响现有节点。
2. 再在每个节点的部署目录 `.env`（例如 `/home/dell/docker/cv-platform-node/.env`）加入同一行，执行 `docker compose -f compose.gpu-node.yaml up -d` 或重启 systemd 单元。

配置后，除 `/health` 外的 Manager 接口（包括 `/docs`）都要求 `Authorization: Bearer <令牌>`。可用下面的命令验证，第一条应返回 401，第二条返回 GPU 列表：

```bash
curl -i http://192.168.8.135:8010/api/v1/system/gpus
curl -H "Authorization: Bearer <令牌>" http://192.168.8.135:8010/api/v1/system/gpus
```

无论是否配置令牌，Manager 都只启动 `cv-platform/` 开头的算法镜像（可用 `ALGORITHM_MANAGER_ALLOWED_IMAGE_PREFIXES` 调整），并拒绝把平台自身的服务镜像当作算法容器运行。

## 平台操作

1. 高级编排 → 运行节点，注册名称与 URL，例如 `dell-gpu`、`Dell RTX 5070 Ti`、`http://192.168.8.135:8010/api/v1`。
2. 工作台 → 运行资源，选择“自动调度”，或指定服务器及该服务器上的具体 GPU。
3. 提交任务，任务详情显示请求的位置、实际执行服务器、实际 GPU 与容器。

指定节点或 GPU 后不会自动转移到其他节点；不存在、停用、离线、GPU 不可检测时返回明确错误。重试保留原选择。自动调度仍按可用节点资源选择，并复用符合设备条件的健康实例。

输入图片由主服务器 Worker 按需通过 HTTP multipart 传到远端 Manager，Manager 将图片临时存入算法可读的 `/data/remote-inputs`，再代理到内部容器 `/predict`；正常返回或异常时清理临时文件。无需共享磁盘，也无需逐个暴露算法容器端口。当前传输覆盖图片输入与标准 JSON 结果，算法额外输出的文件仍需单独提供跨节点访问方式。

## 更新主平台

本次加入数据库迁移 `20261016_0002`。先运行迁移，再更新 Backend 和 Worker；前端与 Manager 也需要同步更新。Compose 的 database-migrator 完成升级后才会启动 Backend/Worker。

```bash
docker compose -f compose.yaml -f compose.database.yaml --profile database up -d --build
```
