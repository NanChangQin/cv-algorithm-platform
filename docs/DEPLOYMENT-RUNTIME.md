# ONNX 发布与 RuntimeProfile

当前支持目标检测 FP32 ONNX、ONNX Runtime CPU、单张 RGB 图片、batch=1。支持静态和动态空间尺寸；动态模型的实际输入仍受模型结构和内存限制。动态 batch 图允许导出时 batch>1，服务调用仍固定单图；静态 batch>1 图拒绝发布。PyTorch、其他导入算法继续使用各自适配器。

## 从导出到推理

1. 在导出中心完成 ONNX 转换、ORT 冒烟、同集验证和质量门。
2. 点击“发布为 ONNX 推理版本”，填写模型 ID、名称和版本。留空版本自动递增补丁号。
3. 发布会重新核验全部登记产物的大小和 SHA-256，复制 ONNX、外部张量、报告和校验清单到独立模型目录。删除原导出不影响发布版本。
4. 在部署页选择发布版本、服务器和 RuntimeProfile。ONNX 固定 CPU；PyTorch 等适配器继续按 manifest 选择 CPU/GPU。
5. 平台检查节点、镜像、权重、启动容器，再读取 ONNX 的加载与预热证据。实际模型哈希、provider、线程数、预热次数必须匹配；失败不标记运行中。
6. 在工作台选择部署实例。服务默认参数自动加载，任务持久化部署 ID，执行和重试都使用该部署的确切容器。部署停止或删除后任务会失败，不会自动换容器。

发布接口：`POST /api/v1/exports/{job_id}/publish`。

```json
{"key":"helmet-onnx","name":"安全帽 ONNX","version":"1.0.0"}
```

## RuntimeProfile

部署创建接口：`POST /api/v1/deployments`。配置随部署持久化；需要不同设置时创建新部署。相同模型、同一节点上的不同配置使用独立容器。

```json
{
  "algorithm_version_id": "模型版本 UUID",
  "node_id": "cpu-node",
  "gpu_device_ids": [],
  "runtime_profile": {
    "memory_mb": 1536,
    "cpu_count": 2,
    "shm_mb": 128,
    "prediction_timeout_seconds": 120,
    "threads": 2,
    "warmup_runs": 2,
    "defaults": {"confidence": 0.3},
    "allowed_overrides": ["confidence", "classes"]
  }
}
```

| 字段 | 默认值 | 实际作用与范围 |
| --- | --- | --- |
| memory_mb | null | 使用算法推荐值；显式值 256～262144 MB，传 Docker mem_limit |
| cpu_count | 1 | 0.1～64，传 Docker nano_cpus |
| shm_mb | 64 | 16～16384 MB，传 Docker shm_size |
| prediction_timeout_seconds | 120 | 1～600 秒，同时应用于 Backend 请求和远节点 Manager 代理 |
| threads | 1 | ONNX CPU ORT intra_op_num_threads，1～64；inter_op 固定 1 |
| warmup_runs | 1 | ONNX 启动后实际预测次数，1～10 |
| defaults | {} | 服务默认预测参数，按该模型 manifest 校验 |
| allowed_overrides | null | null 允许覆盖所有声明参数；[] 全部锁定；数组仅开放指定键 |

请求提交锁定参数的默认值可以通过，提交不同值会报错。未知字段、未知参数、无效类型或超范围值会拒绝。Backend 和 ONNX 容器均校验默认值及覆盖策略。其他适配器的覆盖策略由 Backend 执行；它们不支持自定义 threads/warmup_runs。

ONNX 参数面板只开放实际支持的 confidence、iou_threshold、max_detections、image_size、classes、agnostic_nms。静态图的 image_size 固定；动态图支持契约允许的整数或 [高度, 宽度]。预处理使用固定版本 Ultralytics，rect=False，ONNX 图之外执行后处理。FP16/INT8、compile、augment、视频和模型 batch 不在这个入口开放。

就绪证据的 `input_shape` 是图声明的形状（动态维度为符号），`warmup_input_shape` 是实际预热张量形状。预测结果 `configuration` 保留 submitted、normalized、effective；effective.imgsz 读取框架校验后的 [高度, 宽度]，`input_tensor_shape` 记录真正进入 ONNX 前的 NCHW 张量。工作台“恢复默认”使用所选部署的服务默认参数，复用任务时保留 deployment_id 和原任务参数。

ONNX 服务单 worker、单请求并发，避免共享 predictor 状态污染；繁忙返回 429，调用方需要退避后重试。workers、replicas、并发数等尚未开放为可调字段，提交它们会失败。warmup 使用合成 RGB 样本验证执行链路，不能替代原导出的同集精度验证，也不代表业务样本验收或负载测试。

## 服务版本路由与零中断切换

部署服务路由为业务方和工作台提供稳定的调用入口 `service_key`，将调用方与具体的部署实例 UUID 解耦，支持新版本就绪验证后自动切流、旧实例在途请求排空（`draining`）以及故障自动/手动回滚：

- 配置路由：`PUT /api/v1/deployments/routes/{key}`，指定 `target_deployment_id`、`name` 与 `drain_timeout_seconds`（5～3600 秒，默认 120 秒）。
- 首次创建路由时，目标部署实例必须处于 `running` 且已完成就绪核验。
- 更新路由至新部署时：
  - 若目标部署已处于 `running`，立即切换 `active_deployment_id`，原部署記为 `previous_deployment_id`。
  - 若目标部署处于 `queued / preparing / stopped / failed`（其中 `stopped / failed` 会自动触发启动），平台将其登记为 `candidate_deployment_id`（`rollout_status = switching`），期间线上流量继续由原部署承接；当候选部署完成容器启动与 ORT 预热验证进入 `running` 后，协调器自动完成流量切换；若候选部署失败，自动清除候选并记录原因，线上流量不受影响。
- 在途请求排空（`draining`）：旧部署被替换或手动点击停止时，若仍有已綁定该部署的在途任务（`queued / preparing / starting / running`），部署进入 `draining` 状态，拒绝新的直接绑定请求，等待在途任务执行完毕或达到 `drain_timeout_seconds` 后再停止容器。
- 故障恢复与一键回滚：
  - 手动回滚：`POST /api/v1/deployments/routes/{key}/rollback`，若上一版本仍在运行或排空中则立即切回，若已停止则自动排队启动并在就绪后切回。
  - 自动故障恢复：当健康检查发现当前线上部署实例丢失或异常时，若存在可用的 `previous_deployment_id`，自动回退至上一运行版本或触发其重新启动。
- 调用方式：`POST /api/v1/tasks` 支持传入 `"service_key": "helmet-prod"`（工作台“运行位置 → 服务路由入口”可直接选择），由服务端解析当前线上部署并锁定任务绑定。

## 镜像与升级

从仓库根目录构建：

```powershell
docker build -f runtimes/yolo-onnx/Dockerfile -t cv-platform/yolo-onnx:dev .
```

Backend 配置 `CV_PLATFORM_ONNX_INFERENCE_IMAGE`，默认 `cv-platform/yolo-onnx:dev`。镜像使用 Torch 2.10.0 CPU、Ultralytics 8.4.183、ORT 1.23.2。无需 CUDA。镜像必须预先存在于目标节点，或存在于可传输的其他节点。

升级 Backend、Worker、Manager 和 SDK，执行迁移 `20261025_0009` 与 `20261026_0010`。新部署会检查 Manager 的 `/instances/capabilities` 协议，旧节点会明确拒绝。ONNX 镜像还须具备 `cv.platform.runtime-profile=cv-runtime-profile-v1` 标签，避免忽略配置。

历史部署保留空 RuntimeProfile，历史任务保持未绑定；已运行的旧部署仍可按确切实例调用。重新启动的部署获得专属容器。自动扩缩容不计入、不回收专属部署实例；部署协调器继续刷新最近使用时间。Manager 重启通过 Docker 标签恢复部署身份、超时和配置指纹；同一部署配置不一致时要求重新部署。

当前已完成服务版本路由、就绪后流量切换、在途请求排空、故障回滚、在线性能分位数观测（推理/端到端/队列等待 P50/P95/P99、QPS、错误率）以及任务中心错漏样本回流生成新数据集版本；凭证管理、视频/有状态跟踪、真正模型 batch、TensorRT 或量化仍在后续阶段推进。对应 P3/P4 后续工作仍在生命周期评审中，不应标记为全部完成。
