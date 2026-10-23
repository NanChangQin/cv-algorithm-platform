# ONNX 导出阶段验收

日期：2026-10-23。范围：独立 FP32 ONNX 导出、目录产物、ONNX Runtime CPU 冒烟、同集精度比较、质量检查和 CPU 微基准。使用说明见 [EXPORTS.md](EXPORTS.md)。

## 自动回归

| 检查 | 结果 |
| --- | --- |
| Backend 全量，memory | 195 passed |
| Backend 全量，隔离 PostgreSQL 17 | 195 passed |
| Manager 全量 | 63 passed |
| SDK 与 runner 参数测试 | 55 passed |
| Frontend 全量 | 106 passed |
| Frontend 类型检查、生产构建、ESLint | 通过 |
| Backend / Manager Ruff；新增 SDK / export runner Ruff | 通过 |
| 历史数据及迁移 up/down/up、空库全链迁移 | 通过，head 为 20261024_0008 |

PostgreSQL 回归只使用独立临时数据库、内部 Docker 网络及 tmpfs 数据目录。测试容器/网络前缀为 `cv-export-regression-20261023`，已清理。未连接生产数据库。

新增功能测试覆盖：CPU 无 GPU 节点、权重来源冻结、数据/权重哈希、项目隔离、未知及不兼容参数、同集配置限制、静态 batch 与矩形输入、精度失败保留报告/下载、嵌套 sidecar、产物哈希损坏时保留节点副本，以及前端来源选择和表单联动。

Python 测试仍有一项现有 Starlette/httpx 弃用警告，不影响本次结果。最终仅修改参数说明与前端提示后，额外复跑 Backend 导出专项 20 项、Manager 63 项、SDK/runner 55 项与前端全量 106 项，全部通过。

## 真实 CPU 执行

服务器使用独立目录 `/tmp/cv-onnx-20261023/`。未重启/替换生产 Backend、Manager、Frontend 或训练镜像标签。

依赖镜像基于既有 trainer，增加 `onnx==1.19.1`、`onnxruntime==1.23.2`、`onnxslim==0.1.82`；再构建包含本轮 SDK 与 runner 的 `cv-platform/onnx-lifecycle:20261023-r2`。该临时验收镜像的 ID 为：

```text
sha256:0dd7fe417b3e05299f1d725ae2f7c3a48ddd74765e970c2500571fc45890d4fe
```

并非从更新后的 Dockerfile 重新构建完整生产镜像，不能据此声称正式发布完成。最终测试源码包位于本地 `build/exports-20261023/final-source.tar.gz`，SHA-256：

```text
64aa253de20b6f59635fb4a4ee6731af4913bd6f8377088f8fc90dcb9b0379bb
```

运行 `scripts/verify_onnx_export.py`，使用上一阶段真实训练的 YOLO11n `box` fixture checkpoint，train/val/test 各 4 张合成图片；容器断网、非 root、2 CPU/8 GiB、cap-drop ALL、no-new-privileges。固定 Ultralytics 8.4.183、PyTorch 2.10.0+cu128，实际使用 CPU。

最终 r4 结果：

| 模式 | 冒烟输入 | opset | 原始 mAP50-95 | ONNX mAP50-95 | 下降 |
| --- | --- | --- | --- | --- | --- |
| static | 1×3×64×64 | 17 | 0.3716904762 | 0.3716904762 | 0 |
| dynamic | 2×3×64×96 | 17 | 0.2867666667 | 0.2867666667 | 0 |

两种模式均完成真实转换、图检查、ONNX Runtime 冒烟、PyTorch/ONNX 同集验证、预热后测量和清单哈希验证。动态图输入是冒烟时的输入；两侧验证的实际上游参数另存于 `parameter-resolution.json`，不能将所有环节的尺寸都等同于此列。

此 harness 的 `dataset_sha256` 是 fixture 的 `data.yaml` 哈希，仅供运行器测试；下一节平台全链路另行验证真实规范化数据 ZIP 的哈希。合成样本只证明流程执行正确，不能用于业务精度、不同格式优劣或生产性能结论；本次未对 YOLO26、不同 opset、全部导出参数组合逐项实测。

## 平台到真实节点全链路

运行 `scripts/verify_export_lifecycle.py`：隔离 memory Backend → 上传数据 ZIP 与 `.pt` → 创建 `/exports` → Backend 准备与同步 → 真 Manager training router → 真 Docker runner → 产物回收与下载 → 节点容器清理。

临时节点仅监听宿主 `127.0.0.1:18011`，只加载 training router，不启动完整 Manager 的实例回收循环；挂载 Docker socket 只为创建本次验收容器。Backend 通过显式环境变量使用 memory 存储、临时文件目录与该 loopback 节点，不读取生产环境。

最终 r2 作业：`37186bf1-fcb5-4133-a9d8-c5e852719bab`，状态 completed，共收回 6 个登记文件（含清单）。核对：

- 报告内的权重 SHA-256 等于上传来源哈希。
- 报告内的数据 SHA-256 等于平台规范化 ZIP 哈希。
- ONNX Runtime 冒烟和精度检查通过，ONNX 可下载且完整 ZIP 包含该模型。
- 普通训练列表不混入导出作业。
- 产物收回后节点查询该作业返回不存在。

临时 Backend、测试节点与导出运行器均已清理；生产平台容器仍保持原运行状态。PostgreSQL 持久化和真实 Docker 链路分别验收，本次未把真实节点执行与 PostgreSQL 合并为一轮生产形态运行。

## 页面验收

本机启动隔离 memory Backend 与开发前端，用已收回的真实 r1 报告填充只供回读的页面 fixture。浏览器确认：CPU 节点与数据版本选择、8 项导出参数、FP32 提示、静态矩形输入禁用提交、开启 dynamic 后解除限制、精度与耗时表、哈希及报告下载入口。

默认浏览器宽度 714 px 下无页面横向溢出，控制台无 error。未做完整移动端矩阵；本次浏览器没有发起真实转换任务，真实执行由上一节 API harness 验收。截图：`build/exports-20261023/export-browser.png`。本地临时服务在验收后停止。

## 证据与复现

本地目录 `build/exports-20261023/` 保存：

- `runtime-r4.log`、`evidence-r4/VERIFIED.json`、static/dynamic 日志、报告、参数快照与清单。
- `api-r2.log`、`api-r2/evidence/API-VERIFIED.json`、节点访问日志。
- `postgres-r2.log`、镜像构建日志、最终源码包及只运行 training router 的临时节点 harness。

这些是本机验收产物，`build/` 不随 Git 提交；可执行主脚本在仓库 `scripts/`。在已安装依赖、具有可写证据目录和类别匹配权重的隔离 runner 中复现：

```bash
PYTHONPATH=/tests/packages/algorithm-sdk/src \
  python /tests/scripts/verify_onnx_export.py /evidence /fixture.pt
```

平台整链脚本强制要求 `CV_PLATFORM_PERSISTENCE_BACKEND=memory` 和 `CV_PLATFORM_ALGORITHM_MANAGER_URL=http://127.0.0.1:18011/api/v1`，还需把 storage/package root 指向临时目录、配置本轮 trainer 镜像，并提供 `/fixture.pt` 和 `/evidence`。只允许连接隔离测试节点，不能把脚本指向生产服务。

## 当前边界

尚未完成生产部署、正式镜像全量重建、导出产物直接上线推理、RuntimeProfile、TensorRT/OpenVINO/TorchScript、FP16/INT8、服务并发基准及 segment/pose/OBB/classify 的导出适配。
