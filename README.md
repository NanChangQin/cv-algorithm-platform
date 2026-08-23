# CV Algorithm Platform

多算法计算机视觉托管、运行、对比与结果可视化平台。

本目录是多算法平台的独立开发项目。现有 `fasterrcnn_trainer-master` 作为原型和迁移来源保留，不在原目录继续堆叠新平台功能。

## 产品文档

- [多算法 CV 可视化平台 PRD](docs/PRD-多算法CV可视化平台.md)
- [第一轮开发说明](docs/ROUND-1-DEVELOPMENT.md)
- [第二轮开发说明](docs/ROUND-2-DEVELOPMENT.md)
- [第三至第五轮开发说明](docs/ROUND-3-TO-5-DEVELOPMENT.md)
- [PRD 功能覆盖矩阵](docs/FEATURE-COVERAGE.md)
- [本地服务器部署说明](docs/SERVER-DEPLOYMENT.md)

## 目录结构

```text
cv-algorithm-platform/
├── frontend/                    # Vue 3 前端
├── backend/                     # FastAPI 平台 API
├── services/
│   └── algorithm-manager/       # Docker 容器生命周期与资源管理
├── packages/
│   └── algorithm-sdk/           # 算法协议、Schema 与容器服务 SDK
├── runtimes/
│   ├── pytorch/                 # PyTorch 受控基础运行时
│   ├── paddle/                  # PaddlePaddle 受控基础运行时
│   └── onnx/                    # ONNX Runtime 受控基础运行时
├── examples/
│   ├── faster-rcnn/             # 第一个迁移算法
│   └── yolo/                    # 第二个验证算法
├── infra/
│   └── docker/                  # 本地服务器与基础服务部署
├── database/                    # PostgreSQL Alembic 版本迁移
├── docs/                        # PRD、架构和接口文档
└── tests/                       # 跨服务协议与集成测试
```

## 已完成能力

- MVP：安全算法导入、独立 Docker 容器、图片推理、统一结果、多算法对比。
- V1：五类 CV 结果、版本治理、项目级 RBAC、SSE、结果归档、GPU/LRU 治理。
- V2：视频/摄像头/RTSP、多 GPU/多节点、DAG、BentoML/KServe、灰度与自动扩缩容。
- PostgreSQL 业务仓储与 Alembic 版本迁移已启用；本地全栈脚本会自动建库和迁移。

详见 [功能覆盖矩阵](docs/FEATURE-COVERAGE.md) 与 [服务器部署说明](docs/SERVER-DEPLOYMENT.md)。

## 原 MVP 开发顺序

1. 定义算法 `manifest`、容器 API 和统一 Result Schema。
2. 创建 Algorithm SDK 和协议测试。
3. 将 Faster R-CNN 推理拆成第一个独立算法容器。
4. 实现 FastAPI 任务中心和 Algorithm Manager。
5. 接入 YOLO，验证不同依赖环境隔离。
6. 实现同一图片的多算法并排对比。

## 开发原则

- 主平台不直接导入或执行算法 Python 代码。
- 普通用户不能上传任意 Dockerfile。
- Algorithm Manager 是唯一允许访问 Docker Engine API 的服务。
- 所有算法容器统一实现 `/health`、`/metadata` 和 `/predict`。
- 所有结果必须通过统一 Schema 校验后才能进入结果中心。
- MVP 使用单机 Docker，不引入 Kubernetes。

## Docker 本地全栈预览

复制环境变量并修改管理员密码、数据库密码。默认 `CV_PLATFORM_AUTH_ENABLED=false`，浏览器会直接以内置管理员身份进入平台，不显示登录页：

```powershell
Copy-Item .env.example .env
```

一条命令构建并启动 PostgreSQL、数据库迁移器、Algorithm Manager、持久任务 Worker、Media Worker、Backend 与 Frontend：

```powershell
.\scripts\docker-local.ps1 up
```

也可以直接使用 Compose：

```powershell
docker compose -f compose.yaml -f compose.database.yaml --profile database up -d --build
```

启动后访问 `http://localhost:8080`。查看状态、持续日志或停止服务：

```powershell
.\scripts\docker-local.ps1 status
.\scripts\docker-local.ps1 logs
.\scripts\docker-local.ps1 down
```

`down` 不删除 PostgreSQL 命名卷，下一次启动会保留数据库；如需删除数据库数据，应在确认备份后手工删除 `cv-algorithm-platform-postgres` 卷。

首次部署必须在 `.env` 设置强管理员密码：

```text
CV_PLATFORM_ADMIN_USERNAME=admin
CV_PLATFORM_ADMIN_PASSWORD=<至少 12 位强密码>
```

需要恢复登录与会话校验时设置：

```text
CV_PLATFORM_AUTH_ENABLED=true
```

免登录模式仅适合本机或受信任内网；对公网开放前必须恢复登录，并配置 HTTPS 与安全 Cookie。

准备服务器目录和验证项目：

```powershell
.\scripts\prepare-server.ps1
.\scripts\verify.ps1
```

数据库容器会自动完成 Alembic 迁移。用户和会话、项目和成员、算法和构建日志、素材元数据、推理任务和结果、算法对比、媒体源和运行、工作流和运行、运行节点、扩缩容策略及审计均写入 PostgreSQL。图片、视频、模型与算法包本体仍使用受控文件目录；Algorithm Manager 重启后会通过 Docker 标签恢复其管理的容器索引。

数据库组合会启用 PostgreSQL 持久队列和独立 Worker。Backend 重启不会丢失已排队的构建或推理任务，Worker 重启会重新领取中断任务。媒体运行和工作流协调器当前仍在 Backend 进程内，重启后会明确标记为中断，可在界面重试。

完整备份恢复见 [服务器备份与恢复](docs/BACKUP-RESTORE.md)，Prometheus/Grafana 组合见 [运行监控](docs/MONITORING.md)。

数据库版本管理详见 [数据库迁移说明](database/README.md)，支持版本查询、升级、回滚和离线 SQL 导出。
