# 模型导出与导出后验证

当前支持 YOLO 目标检测 `.pt` → **FP32 ONNX**，固定 Ultralytics 8.4.183。转换、原始权重验证、ONNX Runtime 验证及耗时测量均在所选节点的 CPU 上执行，无需 GPU。验收记录见 [EXPORT-VALIDATION.md](EXPORT-VALIDATION.md)。

## 使用

1. 打开侧栏“模型导出”，或从算法库的已发布模型、训练详情的 best/last 入口进入。
2. 选择执行服务器、验证数据集版本，以及内置模型、上传权重、已发布模型或历史 best/last。来源权重被复制并冻结，记录 SHA-256；导出不接受 resume。
3. 配置导出参数与同集验证参数，设置允许的 mAP50-95 绝对下降、CPU 线程数及预热/测量次数。
4. 创建任务，查看转换、冒烟、原始验证、ONNX 验证和耗时测量的阶段及日志。可停止任务。
5. 查看精度差值、CPU 耗时、实际输入形状、opset、依赖版本与文件哈希，下载模型、报告或完整 ZIP。
6. 质量门和冒烟通过后，点击“发布为 ONNX 推理版本”，再到部署页配置 RuntimeProfile。发布副本独立保存，删除原导出不会删除已发布模型。操作与运行限制见 [ONNX 发布与 RuntimeProfile](DEPLOYMENT-RUNTIME.md)。

模型与数据集的类别名称、数量及顺序须完全一致。内置 COCO 模型需要匹配的 COCO 类别映射，不能用任意自定义类别数据直接比较。数据划分可选 train/val/test，所选划分必须存在。

## 参数能力

导出表单与 YAML/JSON 共用 `mode=export` 契约。未知字段、暂未支持字段及不兼容配置会明确报错。

| 参数 | 当前范围 |
| --- | --- |
| `format` | 仅 `onnx` |
| `imgsz` | 整数或两个整数的数组，受公共尺寸 schema 约束；静态图要求正方形；上游 stride 调整见实际形状 |
| `batch` | 1～64；静态图要求所选验证图片数可整除 batch，建议从 1 开始 |
| `dynamic` | 动态 batch / 空间尺寸；动态模式可使用矩形尺寸 |
| `simplify` | 是否使用 onnxslim 简化 |
| `opset` | null 或 13～20；null 使用固定版本转换器默认值，报告保存真实 opset |
| `quantize` | 仅 FP32，包括契约中的等价别名；不接受 FP16/INT8 |
| `verbose` | 转换日志详细程度 |

验证参数消费现有 val 契约，包括 split、conf、iou、classes、max_det、agnostic_nms、绘图与结果保存等。为了公平比较，平台固定两侧的 batch/imgsz 为导出值，`rect=false`、`fraction=1`、FP32、`compile=false`、`augment=false`、`channels_last=false`、`nms=false`，冲突配置会拒绝。框架仍可能调整验证尺寸，详细参数见两侧 effective 快照。

验证策略可配置：`max_map_drop` 为 0～1（默认 0.01，即 1 个百分点）、`warmup` 为 0～20、`iterations` 为 1～100、`threads` 为 1～32。线程设置同时传给 PyTorch 和 ONNX Runtime 内部会话。

CPU 测量使用一个真实样本重复组成所选 batch，包含预处理、模型推理与后处理，报告平均值、P50、P95 和图/秒。它是单进程微基准，**不代表在线服务 QPS、并发能力或完整数据分布下的性能**。warmup=0 时首次模型初始化开销也计入测量。

TensorRT、OpenVINO、TorchScript、FP16/INT8、图内 NMS、校准与其他任务类型尚未接入本阶段；目录中展示具体限制。已通过验证的 ONNX 可发布后部署到 CPU，在线服务每次执行 batch=1。静态 batch>1 的图不能发布到这个服务；动态 batch 的图允许从较大的导出 batch 发布，实际调用仍为单图。

## 报告与失败

运行器先校验 ONNX 图，再用 ONNX Runtime CPU 执行一次真实推理并检查有限数值，然后以同一数据和配置分别运行 `.pt` 与 ONNX 验证。

质量检查使用 `原始 mAP50-95 − ONNX mAP50-95 <= max_map_drop`。超过阈值时任务标为失败，但已经生成的报告和已登记模型仍可下载，方便分析。转换或运行时加载等更早阶段失败时，查看错误和日志；不保证存在完整报告。

产物包括：

- `model/model.onnx` 及转换器产生的 sidecar。
- `export-report.json`：来源/数据/模型哈希、类别、实际形状、opset、运行库版本、精度比较、质量检查和耗时。
- `parameter-resolution.json`：原始提交、规范化导出参数、转换器实际参数，以及两侧验证的实际参数。
- `reference-evaluation.json`、`onnx-evaluation.json`，以及按配置生成的验证图片/JSON。
- `artifact-manifest.json`：登记相对路径、大小和 SHA-256，支持嵌套目录；拒绝越界、符号链接、大小/哈希错误。

只有已登记文件可从节点下载；源 `.pt` 和任意临时文件不自动暴露。Backend 收回时核对全部文件集合与哈希后才完成任务并清理节点。传输或校验失败时保留节点副本供重试。

## API 与运行环境

```text
GET/POST /api/v1/exports
GET/DELETE /api/v1/exports/{id}
POST /api/v1/exports/{id}/stop
POST /api/v1/exports/{id}/publish
GET /api/v1/exports/{id}/report
GET /api/v1/exports/{id}/logs
GET /api/v1/exports/{id}/archive
GET /api/v1/exports/{id}/artifacts/{relative_path}
GET /api/v1/training/parameter-contract?mode=export
```

创建请求示例：

```json
{
  "name": "ONNX test 验收",
  "dataset_id": "<数据版本 UUID>",
  "node_id": "local",
  "source": {"kind": "run", "reference": "<训练 UUID>", "checkpoint": "best"},
  "parameters": {"format": "onnx", "imgsz": 640, "batch": 1, "dynamic": false, "simplify": true, "opset": 17},
  "verification": {"parameters": {"split": "test"}, "max_map_drop": 0.01, "warmup": 1, "iterations": 5, "threads": 2}
}
```

独立 API/页面复用现有 `training_runs` 持久化和协调器，通过 `progress.operation=export` 分流，普通训练列表隐藏导出。项目隔离与管理员/开发者权限沿用现有机制。导出任务本身无新增迁移；使用 RuntimeProfile 和部署绑定推理需要完成 `20261025_0009`，同时包含先前的数据版本迁移。

必须同步更新 Backend、Frontend、相关 Manager 与训练镜像。训练镜像除参数/来源协议标签外，还须包含 `cv.platform.export-protocol=ultralytics:8.4.183:onnx-v1`。固定依赖为 `onnx==1.19.1`、`onnxruntime==1.23.2`、`onnxslim==0.1.82`，运行时关闭自动安装、默认断网；缺依赖应重建镜像。

正式上线前备份数据库和 training-store，并核对各节点镜像及协议标签。测试镜像或挂载源码的验收不等于生产服务已经升级。
