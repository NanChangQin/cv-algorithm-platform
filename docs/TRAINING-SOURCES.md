# 自定义权重、微调与断点续训

当前支持 Ultralytics **8.4.183** 的目标检测 `.pt` 权重。模型来源、训练参数、数据集版本与内容哈希在创建任务时固定；来源文件被删除，不影响已经创建的任务。

## 选择模型来源

在训练中心的新建表单中选择内置预训练模型，或切换到受管权重：

| 来源 | 使用方式 |
| --- | --- |
| 内置模型 | 选择 YOLO11 / YOLO26 的预置检测权重 |
| 上传权重 | 上传非空 `.pt` 文件，最多 2 GiB；显示文件名与 SHA-256 |
| 已发布模型 | 选择算法库中由训练发布、仍有受管 `.pt` 文件的版本 |
| 历史训练 | 选择已结束训练的 `best.pt` 或 `last.pt`，创建微调任务 |

算法库版本和训练详情均提供“微调”入口。微调建立新的训练计划，重置 optimizer、AMP 和轮次，可以选择其他数据集版本并调整全部已开放的训练参数。

上传接口只检查大小、扩展名并计算哈希，不在 Backend 或 Manager 中反序列化 PyTorch 文件。模型类型、文件可加载性和框架兼容性在隔离运行容器中检查；列表中的 `pending_runtime` 表示尚未完成运行时检查。分割、分类、姿态、OBB、ONNX 及任意自定义网络包不在本入口支持范围内。

## 真正断点续训

训练详情显示完整恢复点及可用状态。对中断、停止或失败但已收回完整 checkpoint 的训练，点击“断点续训”会创建一条新记录：

1. 固定原数据集版本与 SHA-256，固定原计划总轮数、优化器和训练超参数。
2. 可以更换训练节点及 GPU；当前需要选择单张 GPU。
3. 只允许调整 `batch`、`imgsz`、`workers`、`cache`。其他参数变更会明确拒绝，应改用微调入口。
4. 恢复 epoch、optimizer、AMP scaler、EMA、EMA updates 和 early-stopping 状态，重建历史 `results.csv`，继续下一轮。
5. 保留原 `best.pt`，新训练中只有出现更好的验证结果才由框架更新最佳权重。自动优化器使用原运行的实际类型和初始学习率，避免更改 batch 后重新选型。

这是**最近完整保存轮次**的恢复，不承诺逐 batch 或逐位一致。停止容器不会额外等待当前轮完成；当前轮尚未提交的状态会丢失。数据加载器和随机数状态没有作为逐 batch 快照保存。

Ultralytics 在最终评估时会剥离常规 `last.pt` / `best.pt` 的 optimizer 和 epoch，所以平台另存：

```text
weights/resume-000001.pt  # 完整 checkpoint；只保留最新已提交的一代
checkpoint.json          # 指向该代的路径、哈希、轮次、实际 optimizer/lr0 和可恢复状态
resume-restoration.json  # 续训启动时核对后的恢复状态
```

先原子提交新 `.pt`，再提交元数据，最后删除旧代；复制中途被终止时保留上一份一致的恢复点。只有保存成功的 checkpoint 才能恢复。`save=false` 不保证中断前存在恢复点，按 `time` 训练不提供续训入口。达到原计划总轮数后，请用 best/last 开始新的微调实验。旧训练记录没有完整恢复点时，也只能微调。

## 存储、接口与升级

Backend 使用现有 `training_runs.progress` JSONB 持久化 `source`、`operation` 和 `checkpoint`；本功能不增加数据库迁移。来源快照在节点进度同步、任务重启准备及发布模型 lineage 中保留。

```text
GET    /api/v1/training/sources
GET    /api/v1/training/weights
POST   /api/v1/training/weights                 # multipart file
DELETE /api/v1/training/weights/{weight_id}
POST   /api/v1/training/runs
```

创建微调请求示例（UUID 替换为当前项目的真实 ID）：

```json
{
  "dataset_id": "00000000-0000-0000-0000-000000000001",
  "node_id": "local",
  "gpu_device_ids": ["0"],
  "source": {
    "kind": "upload",
    "reference": "00000000-0000-0000-0000-000000000002",
    "mode": "finetune"
  },
  "parameters": {"epochs": 50, "batch": 8, "imgsz": 640}
}
```

续训时 `source` 使用 `kind=run`、`reference=<原训练 UUID>`、`checkpoint=resume`、`mode=resume`；`parameters` 只需提交要更改的资源参数。

- 上传权重：`training-store/weights/<project>/<weight>/model.pt` 与 `metadata.json`。
- 任务独立副本：`training-store/runs/<run>/.source/model.pt`；续训可另有 `.source/best.pt`。删除原权重或原训练，不会删除副本。
- 节点缓存：`training/weights/<sha256>.pt`，传输与启动前检查哈希，运行容器只读挂载 `/input/model.pt` 和可选 `/input/best.pt`。删除平台上传记录不会自动回收各节点的哈希缓存。

备份应包含数据库、整个 `training-store` 和发布模型的 `model-store`，仅备份数据库无法恢复权重。独立副本与节点缓存会占用额外磁盘空间。

主平台和各 GPU 节点的 Manager、共享 SDK、训练镜像必须一起更新。新镜像需声明 `cv.platform.training-protocol=ultralytics:8.4.183:sources-v1` 及当前参数契约标签；缺少协议或哈希不一致时明确失败。自定义权重、微调、续训和独立验证当前均限制单卡。

相关：[训练中心](TRAINING.md)、[独立验证](VALIDATION.md)、[本阶段验收](LIFECYCLE-VALIDATION.md)。
