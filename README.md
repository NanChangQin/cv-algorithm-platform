# CV Algorithm Platform

计算机视觉算法平台，提供数据集管理、模型训练、模型发布、图片与视频推理、效果评测和报告下载。前端使用 Vue 3 + TypeScript，平台 API 使用 FastAPI，算法在独立 Docker 容器中运行，业务数据由 PostgreSQL 保存。

## 平台能力

| 模块 | 主要功能 |
| --- | --- |
| 模型训练 | 训练中心、独立训练任务列表、算法与规格选择、参数配置、微调、从头训练、断点续训、独立验证、实验对比、模型导出 |
| 模型推理 | 推理中心、推理任务、图片与批量图片、视频、浏览器摄像头、服务器摄像头、RTSP、结果对比、模型部署 |
| 资源库 | 训练数据集及版本、图片库、问题样本回流、算法与模型版本管理 |
| 平台管理 | 项目成员、用户角色、运行实例、GPU 与节点管理、审计、监控和高级编排 |

### 模型训练

训练中心当前接入**目标检测**，先选择算法，再选择模型规格、权重、数据版本、设备和参数。

| 算法 | 可选规格 | 训练与推理 |
| --- | --- | --- |
| YOLO11 | N / S / M / L / X | 支持训练、验证、发布与推理 |
| YOLO26 | N / S / M / L / X | 支持训练、验证、发布与推理 |
| Faster R-CNN | ResNet50-FPN | 支持 GPU 或显式 CPU 训练，发布后在推理中心使用对应模型 |

- 支持官方预训练权重、自定义权重、历史训练权重、从头训练及完整断点续训。
- 数据集支持 ZIP 导入、图片与标注预览、格式检查、训练/验证/测试划分和版本管理。
- 网页展示训练状态、进度、损失曲线、检测指标、分类别评估与预测样例。
- 支持 CSV/JSON 指标、PNG 图表、PDF 报告、模型权重及完整结果下载。
- 训练完成后可发布到算法库，并在推理中心选择该模型版本验证效果。
- YOLO 支持 ONNX 导出与验证；Faster R-CNN 使用独立参数契约，当前不开放 ONNX 导出。

分类、分割、姿态等其他训练类型尚未接入。推理的统一结果协议支持检测、分类、分割、OCR 和姿态，实际能力取决于已接入的算法。

### 训练任务批量管理

在 **模型训练 → 训练任务** 中勾选任务即可操作：

| 操作 | 行为 |
| --- | --- |
| 多选 | 单项选择、全选本页、选择全部筛选结果、跨页保留选择；更改搜索或筛选条件时清空选择 |
| 批量下载 | 选择模型权重、训练报告（PDF + JSON）、指标 CSV 或完整结果，后台打包成一个 ZIP |
| 批量删除 | 确认范围后删除任务记录及其权重、日志和结果；运行中的任务会跳过，执行前检查依赖 |
| 操作反馈 | 显示进度、当前任务、成功/失败/跳过数量和原因，支持失败重试及刷新后恢复进度 |
| 列表快捷操作 | 每行提供详情、下载和删除入口，每页可显示 10 / 20 / 50 条 |

批量操作单次最多 1000 项；实验对比仍为 2–4 项。ZIP 内按“任务名称 + 短 ID”分目录，并附带逐项操作结果。下载包有效期为 24 小时，过期包会在后续创建批次时清理；原训练结果不受影响。删除训练任务保留数据集和完整的独立发布模型。

详见 [训练中心与批量管理](docs/TRAINING.md)。

### 推理、评测与部署

- 推理中心集中选择模型、输入来源、参数与执行位置，支持自动调度或指定服务器/GPU。
- 推理任务保留状态、结果与运行信息，可查看和下载指标 CSV、完整推理报告及相关产物。
- 评测中心用于有标注数据的模型效果验证；结果对比支持同图多算法并排或叠加查看。
- 支持模型定向部署、多节点资源管理、版本路由、灰度和自动扩缩容。
- 高级能力包含 DAG 工作流，以及 BentoML / KServe 部署文件生成；日常本机运行使用 Docker Compose。

浏览器摄像头依赖采集页面与浏览器授权；服务器摄像头、视频和 RTSP 在服务器侧处理。无标注输入的推理报告不能替代精度评测。详见 [推理中心](docs/INFERENCE.md)、[评测中心](docs/EVALUATION.md) 和 [模型部署](docs/DEPLOYMENT.md)。

## 基本使用流程

1. 在“资源库 → 训练数据集”导入带标注的数据，检查类别和划分。
2. 在“模型训练 → 训练中心”新建训练，选择算法、规格、权重和参数。
3. 在“训练任务”查看进度和效果，下载结果或对比实验。
4. 将合适的训练权重发布为模型版本。
5. 在“推理中心”选择发布的模型，上传图片或配置视频输入，检查实际表现。
6. 需要持续使用时，在“模型部署”选择服务器和运行设备。

## 本机启动与停止（Linux）

以下命令用于**已经完成部署的本机**，在仓库根目录执行。

```bash
# 每次重启后，先手动启动 Docker；如提示密码，输入本机 sudo 密码
sudo systemctl start docker

# 启动平台
bash scripts/platform-local.sh up
```

打开 [http://localhost:8080](http://localhost:8080)。启用登录时使用部署时配置的管理员账号；当前本机使用登录模式。

如果当前终端尚未刷新 `docker` 用户组权限，可使用：

```bash
sg docker -c 'bash scripts/platform-local.sh up'
```

常用管理命令：

```bash
bash scripts/platform-local.sh status       # 查看服务状态
bash scripts/platform-local.sh logs         # 最近 100 行日志
bash scripts/platform-local.sh logs -f      # 持续查看日志
bash scripts/platform-local.sh stop         # 停止服务，保留容器和数据
bash scripts/platform-local.sh down         # 移除平台容器与网络，保留数据卷
```

本机按**手动启停、不随开机启动**配置。`platform-local.sh` 始终叠加 `compose.local.yaml`，将平台容器和受管算法容器的重启策略设为 `no`；Docker 系统服务的开机启动由宿主机单独控制。检查本机配置：

```bash
systemctl is-enabled docker.service docker.socket containerd.service
```

当前配置应返回 `disabled`。`sudo systemctl start docker` 只启动本次服务，不启用开机自启动。

`down` 不删除 PostgreSQL 命名卷及宿主机数据目录。更新代码后需要重新构建镜像，单独执行 `up` 不会重建已有镜像：

```bash
bash scripts/platform-local.sh build
bash scripts/platform-local.sh up
```

### 首次部署

需要 Docker Engine、Compose v2 与 Buildx；GPU 运行还需要可用的 NVIDIA 驱动及 NVIDIA Container Toolkit。Conda 环境用于本机开发和检查，算法依赖位于各自的 Docker 镜像中。

1. 仅首次部署时，复制 `.env.example` 为 `.env`，配置数据目录和端口；已有部署保留原 `.env`。
2. 设置管理员密码、数据库密码；本机访问可将端口绑定为 `127.0.0.1:8080`。
3. 准备宿主机数据、模型和算法包目录，以及所需算法镜像与预训练权重。
4. 启动 Docker，运行 `bash scripts/platform-local.sh up`。缺少平台镜像时脚本会构建，数据库迁移随启动执行。

关键配置示例：

```dotenv
CV_PLATFORM_HTTP_PORT=127.0.0.1:8080
CV_PLATFORM_AUTH_ENABLED=true
CV_PLATFORM_ADMIN_USERNAME=admin
CV_PLATFORM_ADMIN_PASSWORD=<自行设置管理员密码>
CV_PLATFORM_POSTGRES_PASSWORD=<自行设置数据库密码>
```

`.env.example` 的认证默认值为 `false`，会直接以内置管理员身份进入平台；需要登录时设为 `true`。账号密码不写入 README，`.env` 不提交到 Git。

首次部署步骤见 [服务器部署说明](docs/SERVER-DEPLOYMENT.md)；YOLO 与 Faster R-CNN 训练镜像准备见 [训练文档](docs/TRAINING.md)。本机手动启停始终使用上面的 `platform-local.sh`，以包含禁止自动重启的覆盖配置。

## 开发环境

本机 Python 环境使用 Conda，名称为 `cv-platform`，Python 版本为 3.12。已有环境直接激活；新机器可创建：

```bash
conda create -n cv-platform python=3.12 -y
conda activate cv-platform
python -m pip install -e ./packages/algorithm-sdk -e './backend[dev]'
```

前端使用 Node.js 22：

```bash
cd frontend
npm ci
npm run dev
```

前端检查：

```bash
npm test
npm run lint
npm run build
```

回到仓库根目录后，进入 `backend` 运行测试，显式使用内存仓储及独立临时存储：

```bash
cd backend
batch_test_root=$(mktemp -d)
env -u PYTHONPATH -u ALL_PROXY -u all_proxy \
  -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
  CV_PLATFORM_PERSISTENCE_BACKEND=memory \
  CV_PLATFORM_QUEUE_BACKEND=local \
  CV_PLATFORM_STORAGE_ROOT="$batch_test_root/storage" \
  CV_PLATFORM_PACKAGE_ROOT="$batch_test_root/packages" \
  python -m pytest -q
```

后端 PostgreSQL 测试会清空测试数据库，不能连接实际使用中的平台数据库。更多验收步骤见 [验证说明](docs/VALIDATION.md)。

## 目录结构

```text
cv-algorithm-platform/
├── frontend/                   # Vue 3 + TypeScript 界面
├── backend/                    # FastAPI、业务服务与批量任务
├── services/
│   ├── algorithm-manager/      # Docker 生命周期、节点与资源管理
│   └── media-worker/           # 视频与流媒体处理
├── packages/algorithm-sdk/     # 算法协议、参数契约与统一结果 Schema
├── runtimes/
│   ├── yolo-trainer/           # YOLO 训练、验证与导出
│   ├── faster-rcnn-trainer/    # Faster R-CNN 训练与验证
│   └── yolo-onnx/              # ONNX 推理运行时
├── examples/                   # 算法接入示例与推理实现
├── infra/docker/               # 平台服务镜像
├── database/                   # PostgreSQL / Alembic 迁移
├── scripts/                    # 部署、启停、备份与验收脚本
├── docs/                       # 使用、开发与部署文档
└── tests/                      # 跨服务协议与集成测试
```

主平台不直接执行算法 Python 代码；Algorithm Manager 负责访问 Docker Engine。算法服务统一实现 `/health`、`/metadata` 和 `/predict`，结果经 Schema 校验后进入平台。

PostgreSQL 保存业务记录和持久队列；图片、视频、权重、算法包和批量下载包保存在受控文件目录。数据库和文件目录应一起备份。

## 文档索引

- 使用：[训练](docs/TRAINING.md) · [数据集](docs/DATASETS.md) · [推理](docs/INFERENCE.md) · [评测](docs/EVALUATION.md) · [导出](docs/EXPORTS.md)
- 运维：[服务器部署](docs/SERVER-DEPLOYMENT.md) · [模型部署](docs/DEPLOYMENT.md) · [GPU 节点](docs/GPU-NODES.md) · [备份与恢复](docs/BACKUP-RESTORE.md) · [监控](docs/MONITORING.md) · [数据库迁移](database/README.md)
- 需求：[平台 PRD](docs/PRD-多算法CV可视化平台.md) · [功能覆盖矩阵](docs/FEATURE-COVERAGE.md) · [模型训练需求与计划](模型训练.md) · [模型推理需求与计划](模型推理.md)
- 历史开发：[第一轮](docs/ROUND-1-DEVELOPMENT.md) · [第二轮](docs/ROUND-2-DEVELOPMENT.md) · [第三至第五轮](docs/ROUND-3-TO-5-DEVELOPMENT.md)
