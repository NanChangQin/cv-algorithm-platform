# RuntimeProfile 验收记录（2026-10-23）

本阶段完成独立 ONNX 发布、CPU 部署、RuntimeProfile、启动验证证据、确切部署任务路由，以及服务版本路由（就绪后切换、在途请求排空与故障回滚）。

## 本地与数据库回归

- Backend memory 全量 216 项通过（含服务版本路由、候选就绪切流、排空、自动/手动回滚、在线性能分位数观测与样本回流生成新数据集版本），包括旧 Manager 协议拒绝、动态 batch 图单图发布和确切部署路由。
- Manager 67 项通过，包括资源透传、部署隔离、扩缩容隔离、旧镜像拒绝和启动失败清理。
- SDK/runner 57 项通过，包括整数/矩形尺寸的规范化回显及实际输入张量记录。
- Frontend 113 项通过（含服务版本路由配置、回滚、工作台服务入口调用、部署性能指标与任务中心样本回流），包括 CPU/内存/shm/超时/线程/预热/锁定策略提交、导出发布后的部署跳转、任务复用保留部署绑定，以及恢复服务默认参数。
- Vue/TypeScript 生产构建、ESLint 和变更文件 Ruff 检查通过。
- 独立 PostgreSQL 全量 215 项通过；执行 V9→V10→V9→V10、V8→V10 及完整 base→head 重建回归，未连接生产数据库。

## 服务器隔离验证

测试目录 `/tmp/cv-runtime-20261023/`，使用新建网络、独立 Manager、临时 PostgreSQL 和专用 CPU 镜像。Manager 测试入口不执行生产实例发现、全局回收或后台健康调度。

镜像由 `runtimes/yolo-onnx/Dockerfile` 完整构建：

`cv-platform/yolo-onnx:runtime-test-20261023`

Docker image inspect ID：`sha256:88ecfea6929b53ab6ddd37a2d9a0fcb357e31b1c139c5385b51661a0d31d85de`。

真实验证入口 `scripts/verify_runtime_profile.py`：

1. 复制前阶段已验证的真实导出作为输入，调用发布 API，再删除原导出。
2. 同一模型建立两份配置，分别使用 confidence=0.2/0.6，验证独立容器。
3. 读取 Docker inspect：memory=1536 MB、CPU=2、shm=128 MB。
4. 读取 ORT 就绪证据：CPUExecutionProvider、线程=2、预热=2、模型 SHA-256。
5. 通过远节点 multipart 代理实际预测，核对任务部署 ID、容器 ID、实际 conf/imgsz、NCHW 输入张量及预热张量形状。
6. 锁定参数的不同值在创建任务时拒绝。
7. 停止/启动复用该部署实例；重新部署使用新实例；删除后节点实例列表为空。

两份独立结果报告均已通过：

| 场景 | 图声明 | 两个服务的实际输入张量 | 实际 confidence |
| --- | --- | --- | --- |
| STATIC-VERIFIED.json | [1, 3, 64, 64] | [1, 3, 64, 64] / [1, 3, 64, 64] | 0.2 / 0.6 |
| DYNAMIC-VERIFIED.json | [batch, 3, height, width] | [1, 3, 64, 96] / [1, 3, 96, 96] | 0.2 / 0.6 |

动态图使用导出时 batch=2、imgsz=[64,96] 的真实产物，第二个服务显式覆盖 image_size=96，证明服务以 batch=1 调用且尺寸覆盖生效。预热张量与对应服务实际预测张量一致。动态模型的 args.imgsz 原本保留整数；当前 effective.imgsz 读取 predictor 已校验的二维尺寸，同时保存预处理后的真实张量，避免把表示差异或请求值当作执行证据。

就绪预热使用合成图片；转换精度来自前阶段独立导出的同集验证。未宣称生产流量、并发性能或真实业务图片验收通过。

本地证据目录：`build/runtime-profile-20261023/`，保存报告、镜像构建日志、隔离验证日志及运行脚本。该目录不提交。

## 验收边界

本轮没有升级、重启或修改生产平台。验证前后生产 Backend、Worker、Frontend、Manager 和 PostgreSQL 的容器 ID 与启动时间保持一致。远端临时节点、推理容器、测试数据库和网络在验证后清理，专属网络及其容器列表为空；镜像和证据保留。

浏览器工具的 URL 安全检查拒绝访问本地页面，本轮没有浏览器实测或截图验收；没有改走其他浏览器或绕过检查。页面交互由 110 项前端测试和构建校验覆盖。本地临时 Vite/Backend 已停止。
