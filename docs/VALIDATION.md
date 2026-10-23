# 独立验证评估

训练中心支持独立执行 Ultralytics `YOLO.val()`，使用固定模型权重评估指定的数据集版本。独立验证不创建 optimizer，也不修改权重，可用于保留测试集的最终评估或模型版本复测。

## 页面流程

1. 在训练中心点击“独立验证”，或从历史训练、算法库模型版本进入验证。
2. 选择内置模型、上传权重、已发布模型，或历史训练的 best/last；来源使用与微调相同的项目隔离和哈希快照。
3. 选择数据集版本、服务器及一张 GPU。选择 `split=train/val/test`；没有图片的划分不能提交。
4. 使用统一参数表单或 YAML/JSON 配置验证参数，提交后在任务详情查看进度、日志、报告及产物。

支持 **25 项**验证参数，例如 `batch`、`imgsz`、`conf`、`iou`、`max_det`、`classes`、`augment`、`plots`、`save_json`、`save_txt` 和 `split`。表单与 API 使用独立 val 契约，不接受 epochs、lr0 等训练参数。`batch` 必须为正整数；`fraction` 为 0 到 1 的标量。模型、路径、设备由平台选择器管理；PyTorch 权重不开放 OpenCV `dnn` 后端。

验证使用模型固有类别语义：数据集类别名称与顺序必须与模型一致；确实要把全部类别合并验证时，可显式使用 `single_cls=true`。在不同类别数据上适配模型应先微调。

## 报告与参数回读

详情页提供：

- 整体 Precision、Recall、mAP50、mAP50-95。
- 分类别实例数、Precision、Recall、F1、mAP50 和 mAP50-95。
- 各处理阶段的速度，单位 ms/image。
- 混淆矩阵、PR/F1/P/R 曲线和验证样例图，以及原生 JSON/TXT 结果（开启相应保存参数时）。
- `evaluation.json` 原始报告，记录框架版本、split、数据集 SHA-256 和权重 SHA-256。
- `parameter-resolution.json`，记录提交、规范化和实际 validator 参数。

`plots=false` 时上游不计算混淆矩阵，报告返回 `null` 并说明原因。验证指标的阈值、类别过滤、增强和精度设置会影响结果，比较两次实验时应核对参数及数据哈希。报告中的速度来自本次验证，不是独立的部署性能 benchmark。

现有训练详情的指标卡复用单行 `results.csv`；该行的 epoch=1 是显示兼容字段，不表示执行了训练。

## API

```text
GET  /api/v1/training/parameter-contract?mode=val
POST /api/v1/training/runs
GET  /api/v1/training/runs/{run_id}/evaluation
```

创建示例：

```json
{
  "operation": "val",
  "name": "模型 v2 保留测试集验证",
  "dataset_id": "00000000-0000-0000-0000-000000000001",
  "node_id": "local",
  "gpu_device_ids": ["0"],
  "source": {
    "kind": "algorithm",
    "reference": "00000000-0000-0000-0000-000000000002"
  },
  "parameters": {"split": "test", "batch": 8, "imgsz": 640, "plots": true}
}
```

模型与数据集 ID 须属于当前项目。报告接口只有在 `evaluation.json` 被收回并登记为任务产物后才可读取；其他下载、停止和删除操作复用现有训练任务接口。验证任务不能使用 `resume`。

当前仅支持目标检测 `.pt`、单卡；上线前需要同步升级 Backend、Frontend、Manager 与训练镜像。升级与备份要求见 [模型来源与续训](TRAINING-SOURCES.md)。

参考：[Ultralytics 验证模式](https://docs.ultralytics.com/zh/modes/val/)。
