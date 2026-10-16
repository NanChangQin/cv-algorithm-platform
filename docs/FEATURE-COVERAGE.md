# 功能覆盖矩阵

本文档以 `PRD-多算法CV可视化平台.md` 为验收基线。MVP、V1、V2 产品功能均已形成 API、领域服务和前端入口。Docker 数据库组合会启动 PostgreSQL、自动执行 Alembic 迁移并启用业务仓储；图片、视频、模型和算法包本体仍保存在受控文件目录。Redis、MinIO 和 SQLite 当前未引入。

## MVP 与通用需求

| PRD 功能 | 状态 | 实现说明 |
|---|---|---|
| FR-ALG-001 算法列表 | 已实现 | 名称/类型/状态筛选；版本独立；创建者、容器状态、最近调用时间 |
| FR-ALG-002 安全 ZIP 导入 | 已实现 | 五种任务模板下载与页面内接入规范；大小、文件数、压缩比、路径穿越、符号链接、设备文件、自定义 Dockerfile 拒绝 |
| FR-ALG-003 异步构建与日志 | 已实现 | PostgreSQL 持久排队与独立 Worker；受控模板构建；顺序日志；健康、metadata、predict 验收 |
| FR-ALG-004 版本治理 | 已实现 | 启用、停用、回滚、任务引用删除保护；删除前移除全部容器；镜像删除需显式确认 |
| FR-PKG-001~003 包规范 | 已实现 | Pydantic 严格清单、镜像内依赖、三接口协议测试 |
| FR-AST-001~003 图片资源 | 已实现 | JPG/JPEG/PNG/BMP/WebP 多重校验；SHA 复用；受控本地目录；批量上传/下载 |
| FR-TSK-001~006 推理任务 | 已实现 | 完整状态机、动态参数、异步队列、取消、错误码、筛选、重试、SSE |
| FR-CTR-001/002 启动与复用 | 已实现 | Manager 幂等 ensure、版本锁、健康实例 LRU 轮转、多副本 |
| FR-CTR-003 空闲回收 | 已实现 | 默认 30 分钟；后台回收；管理员手动停止；扩缩容冷却窗口 |
| FR-CTR-004 容器隔离 | 已实现 | CPU/内存/PID/GPU、只读根、非 root、cap drop、no-new-privileges、内部网络 |
| FR-CTR-005 健康检查 | 已实现 | 启动等待；持续探测；连续失败阈值；失败停止并标记 |
| 统一算法协议 | 已实现 | SDK 固化 `/health`、`/metadata`、`/predict` 与 manifest 参数协议 |
| 五种 Result Schema | 已实现 | 检测、分类、分割、OCR、姿态的严格可辨识联合类型 |
| FR-VIS-001/002 可视化 | 已实现 | 五类渲染器、图层/类别/置信度、缩放、原图、JSON/PNG 下载 |
| FR-VIS-003 多算法对比 | 已实现 | 并排/叠加、参数、版本、推理耗时、统一结果展示 |
| 结果批量归档 | 已实现 | 最多 100 个任务结果 JSON + 参数/版本 manifest ZIP |
| 认证、RBAC、项目权限 | 已实现（PostgreSQL） | 支持可配置免登录管理员模式；启用认证后使用 admin/developer/user、项目 owner/editor/viewer、持久化会话及 `project_id` 隔离 |
| 用户管理 | 已实现（PostgreSQL） | 用户创建、角色更新、启用/停用、会话撤销、项目成员管理 |
| 审计与限流 | 已实现 | 写操作审计持久化到 PostgreSQL；上传/构建/推理使用进程内滑动窗口限流；安全错误响应 |
| GPU、容器、日志 | 已实现 | GPU/显存、实例、节点、具体 GPU 索引、容器日志、审计事件 |

## V1

| V1 功能 | 状态 | 实现说明 |
|---|---|---|
| 分类、分割、OCR、姿态 | 已实现 | SDK Schema 与前端渲染器完整；分割 Mask 使用受限 PNG data URI/平台制品 URI；算法可统一导入 |
| 版本启停、回滚、删除保护 | 已实现 | 版本与任务引用、容器、镜像联动 |
| 项目级权限 | 已实现（PostgreSQL） | 请求项目上下文、成员角色、跨项目 ID 防护和持久化成员关系 |
| JSON、渲染图、批量 ZIP | 已实现 | 单结果 JSON/Canvas PNG；多结果 ZIP |
| 显存估算、等待队列、LRU | 已实现 | 节点/GPU 空闲显存打分；异步任务队列；最少近期使用实例路由与空闲回收 |
| SSE 事件 | 已实现 | Cookie 鉴权、项目校验、Nginx 禁止缓冲；前端轮询降级 |
| PostgreSQL 迁移基础设施 | 已实现并启用 | Alembic 单一版本链、23 张业务表、升级/回滚、离线 SQL 和独立数据库 Compose |
| PostgreSQL 业务仓储 | 已实现并启用 | 用户/会话、项目/成员、算法/构建、素材、任务/结果、对比、审计、媒体、工作流、运行节点和扩缩容策略均已持久化 |
| MinIO / Redis | 按需后续接入 | 当前使用受控本地文件目录和 PostgreSQL 持久任务队列；仓储、存储和队列边界保持隔离 |

## V2

| V2 功能 | 状态 | 实现说明 |
|---|---|---|
| 视频文件 | 已实现 | 受控 MP4/AVI/MOV/MKV/WebM 上传，OpenCV 抽帧，逐帧异步推理 |
| 摄像头与 RTSP | 已实现 | `camera:N`、RTSP/RTSPS 媒体源，媒体运行与状态展示 |
| 多 GPU 调度 | 已实现 | 工作台可自动调度或指定服务器与 GPU；严格匹配实例设备；支持 DeviceRequest / NVIDIA runtime；请求与实际执行位置持久化 |
| 多服务器调度 | 已实现 | 多 Manager 节点注册、容错发现、跨节点副本分配；远端 Manager 代理推理、按需传图与清理；独立 GPU 节点 Compose，详见 GPU-NODES.md |
| DAG 工作流 | 已实现 | 有环校验、串行/并行拓扑执行、运行列表、取消与失败传播 |
| BentoML 适配 | 已实现 | 生成当前 class-based Service ASGI 包装器、bentofile 和构建命令 |
| Kubernetes/KServe 适配 | 已实现 | 生成安全 InferenceService、资源请求/限制、HPA 配置和部署命令 |
| 监控与灰度 | 已实现 | 任务/延迟/实例指标；确定性权重路由；版本灰度发布控制台 |
| 自动扩缩容 | 已实现 | 活跃任务/目标并发计算；min/max；idle 冷却；多节点真实副本协调 |

## 当前持久化边界与限制

`compose.database.yaml` 会为 Backend 设置 `CV_PLATFORM_PERSISTENCE_BACKEND=postgres`。本地全栈脚本和服务器部署均使用该组合，API 重启后会保留业务数据与登录会话。

- PostgreSQL 保存用户/会话、项目/成员、算法/构建日志、素材元数据、推理任务/结果、算法对比、审计、媒体运行、工作流运行、运行节点、灰度权重和扩缩容策略。
- 图片、视频、算法包和模型文件保留在宿主机受控目录；数据库记录与文件目录应作为同一个恢复点备份。
- Algorithm Manager 会通过 Docker 标签重新发现受管容器。
- 推理和构建由独立 Worker 从 PostgreSQL 原子领取；Backend 重启不影响排队任务，Worker 重启会重新领取中断任务。媒体运行与工作流协调器暂仍在 Backend 进程内。
- 当前持久 Worker 面向单主服务器部署，可调用多个局域网 GPU 节点；图片输入按需传输，不要求共享目录。算法镜像和模型需在目标节点预先准备；额外输出文件的跨节点访问、多 Worker 横向扩展仍需后续完善。
- 不叠加数据库组合时仍可显式使用 `CV_PLATFORM_PERSISTENCE_BACKEND=memory` 进行轻量开发，但不适合作为服务器长期运行模式。
