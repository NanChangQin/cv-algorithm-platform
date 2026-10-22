# 训练中心

在平台里完成“上传数据集 → 选择服务器和 GPU 训练 → 看曲线和日志 → 下载权重”。首版支持 YOLO 目标检测格式，训练框架为 Ultralytics（YOLO26 / YOLO11）。

## 使用流程

1. **训练中心 → 数据集**：上传 ZIP。平台检查每一行标注，把目录整理成统一结构，给出每个类别的标注框数量和导入提示（自动划分的验证集、被删除的错误标注行等）。
2. **新建训练**：选择数据集、预训练模型、训练服务器和 GPU，调整轮数、batch、图片尺寸、学习率等参数。只列出检测到 GPU 的服务器；GPU 选“自动”时使用空闲显存最多的一张。
3. **训练详情**：进度（第几轮、第几个批次、预计剩余时间）、验证集指标曲线（mAP50-95、mAP50、精确率、召回率）、各项损失的训练集/验证集曲线、实时日志。曲线可切换为数据表。
4. **停止**：立即结束训练容器，已经保存的 `last.pt`、`best.pt` 保留，可以照常下载。
5. **结束后**：权重、`results.csv`、`args.yaml` 和评估图表（混淆矩阵、PR 曲线、验证集预测样例）收回主服务器，可单独下载或打包下载；训练服务器上的容器和临时文件随即清理。

训练中心对管理员和算法开发者开放。

## 发布为推理模型

训练结束（完成或手动停止）后，在训练详情点 **发布为推理模型**：

- 选择 `best.pt`（验证集 mAP50-95 最好的一轮）或 `last.pt`（最后一轮），填写模型 ID 和名称；版本号留空时按同一模型 ID 的最高版本递增（第一次为 1.0.0）。
- 发布后成为算法库里的一个可用版本：推理参数与 YOLO 示例一致，默认推理尺寸取训练时的图片尺寸；使用通用推理镜像 `cv-platform/yolo:dev`（`examples/yolo`，与训练镜像同一 PyTorch / Ultralytics 版本，`CV_PLATFORM_YOLO_INFERENCE_IMAGE` 可修改）。
- 权重和该版本的 manifest 复制到主服务器的 `<数据目录>/model-store/<版本 ID>/`；部署或推理需要在某台服务器上启动容器时，平台按 SHA-256 把它们同步到该服务器的 `models/trained/<版本 ID>/`，容器只挂载这个目录。删除训练记录不影响已发布的模型。
- 版本记录来源训练、数据集、基础模型、checkpoint、训练参数和验证集指标。算法库的版本详情显示这些信息，同一模型 ID 的各版本列成对比表（指标最好的高亮），可以直接“回滚到此版本”。
- 发布后可以直接“部署到服务器”（见 [DEPLOYMENT.md](DEPLOYMENT.md)）或“在工作台试用”。

推理镜像需要先构建到至少一台服务器，部署到其他服务器时会自动传输：

```bash
docker compose --profile algorithm-images build yolo \
  --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
```

模型 ID 不能与导入的算法相同；同一 ID 下只放训练发布的版本，保证对比和回滚有意义。迁移 `20261023_0007` 给 `algorithm_versions` 增加 `model_directory`、`lineage` 两列。

## 数据集格式

```text
data.yaml              # names: [person, car]  或  names: {0: person, 1: car}
images/train/*.jpg     labels/train/*.txt
images/val/*.jpg       labels/val/*.txt
```

- 也接受 Roboflow 导出的 `train/images`、`valid/images`，以及 COCO 风格的 `train2017`、`val2017` 目录；ZIP 外面多套一层目录也可以。
- 没有 `data.yaml` 时读取 `classes.txt` / `obj.names`；都没有时按编号生成类别名并提示。
- 没有验证集时按文件路径散列自动抽取约 10% 作为验证集（结果固定，重复导入划分一致）；只有测试集时用测试集验证。
- 每行标注 `类别 cx cy w h`（0~1 归一化），多边形（分割标注）也可以，训练检测模型时会换算成外接框。格式错误的行会删除并列出示例；类别编号超出类别名称数量会拒绝导入。
- 默认上限 10 GB、20 万个文件（`CV_PLATFORM_MAX_DATASET_BYTES`、`CV_PLATFORM_MAX_DATASET_FILES`）。前端 nginx 对这个上传接口单独放宽到 10 GB 并边收边转发。

## 训练服务器准备

训练在已注册的 GPU 节点上运行（见 [GPU-NODES.md](GPU-NODES.md)），需要：

1. **新版 Algorithm Manager**：提供数据集接收、训练容器和产物下载接口。主平台和节点都要更新 Manager 镜像。
2. **训练镜像** `cv-platform/yolo-trainer:dev`：

```bash
# 在训练服务器上构建（需要能访问 Docker Hub 与 PyPI；国内可传入 PyPI 镜像源）
docker compose --profile algorithm-images build yolo-trainer \
  --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
```

镜像基于 PyTorch 2.10 / CUDA 12.8，支持 RTX 50 系列。预训练权重（YOLO26n/s/m、YOLO11n/s/m）和绘图字体在构建时下载进镜像，训练容器默认**断网运行**（`ALGORITHM_MANAGER_TRAINING_NETWORK=none`）。构建机器访问不了 GitHub 时，先在能联网的机器上下载这些文件放进 `runtimes/yolo-trainer/assets/`（文件清单见该目录的 README），构建时直接使用。

也可以在一台机器上构建后 `docker save` / `docker load` 到其他节点。镜像名可用 `CV_PLATFORM_TRAINING_IMAGE` 修改，必须以 `cv-platform/` 开头。

## 运行方式

- 训练容器：非 root（uid 10001）、`cap_drop=ALL`、`no-new-privileges`、默认无网络；数据集只读挂载，只有本次训练的输出目录可写；共享内存默认 2 GB（`ALGORITHM_MANAGER_TRAINING_SHM_MB`），供多进程读图使用。
- 只把所选 GPU 交给容器，容器内编号从 0 开始。
- 数据集按内容校验值传到节点，同一数据集再次训练不会重复上传。
- 主服务器的后台协调负责准备训练、每 3 秒同步一次进度和指标、训练结束后收回产物。多个 Backend 进程时通过 PostgreSQL advisory lock 只让一个进程负责。准备过程中平台重启，任务会重新排队并从头准备；训练服务器暂时连不上时只显示提示，恢复后继续同步。
- 节点上的文件：`<数据目录>/training/datasets/<数据集 ID>`、`<数据目录>/training/runs/<训练 ID>`；主服务器保存在 `<数据目录>/training-store/`。

## 数据库

迁移 `20261023_0005` 新增 `training_datasets`、`training_runs` 两张表。有训练记录的数据集不能删除。
