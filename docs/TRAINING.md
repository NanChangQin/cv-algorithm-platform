# 训练中心

在平台里完成“数据集版本 → 训练/微调/续训 → 独立验证 → 发布为推理模型 → 部署”。当前支持 YOLO 目标检测格式，训练框架固定为 Ultralytics 8.4.183（YOLO26 / YOLO11）。

## 使用流程

1. **训练中心 → 数据集**：配置划分与校验模式后上传 ZIP。可以创建新的数据集，或选择已有版本上传新版本。详情页可切换版本、预览图片和标注、查看三划分统计与校验报告，下载原包和规范化包。详见 [数据集版本与校验](DATASETS.md)。
2. **新建训练**：选择数据集版本、内置模型或受管权重、训练服务器和 GPU，调整轮数、batch、图片尺寸、学习率等参数。支持上传 `.pt`、已有发布模型与历史 best/last 微调，详见 [模型来源与续训](TRAINING-SOURCES.md)。只列出检测到 GPU 的服务器；GPU 选“自动”时使用空闲显存最多的一张。
3. **训练详情**：进度（第几轮、第几个批次、预计剩余时间）、验证集指标曲线（mAP50-95、mAP50、精确率、召回率）、各项损失的训练集/验证集曲线、实时日志。曲线可切换为数据表。
4. **停止与续训**：立即结束训练容器，已经保存的权重保留。存在完整恢复 checkpoint 时，可从最近保存轮次续训；原数据版本及计划固定，只开放资源参数调整。常规 last/best 用于微调，不能替代完整恢复点。
5. **结束后**：权重、`results.csv`、`args.yaml` 和评估图表（混淆矩阵、PR 曲线、验证集预测样例）收回主服务器，可单独下载或打包下载；训练服务器上的容器和临时文件随即清理。
6. **独立验证**：选择模型权重与数据集 train/val/test，配置验证参数，输出整体及分类别指标、速度、混淆矩阵与 JSON 报告。详见 [独立验证评估](VALIDATION.md)。
7. **导出并验证**：从已保存的 best/last 或算法库发布模型进入“模型导出”，配置 FP32 ONNX 参数，自动完成 CPU 转换、ONNX Runtime 冒烟、同集精度比较与耗时测量。详见 [模型导出](EXPORTS.md)。

训练中心对管理员和算法开发者开放。

参数现已接入统一契约：可搜索表单、YAML/JSON 导入导出、复杂类型校验和实际生效快照，详见 [参数配置与生效回读](PARAMETERS.md)。训练详情中的配置快照与 `parameter-resolution.json` 用于核对 AutoBatch、自动优化器和实际学习率。升级时需要同步重建训练镜像并更新 Manager；旧镜像缺少参数契约标签会明确报错。

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
images/test/*.jpg      labels/test/*.txt  # 可选；独立保留
```

- 也接受 Roboflow 导出的 `train/images`、`valid/images`，以及 COCO 风格的 `train2017`、`val2017` 目录；ZIP 外面多套一层目录也可以。
- 没有 `data.yaml` 时读取 `classes.txt` / `obj.names`；都没有时按编号生成类别名并提示。
- 默认保留已有 train / val / test；缺少 val 时，仅从 train 按训练∶验证比例划分（默认 80∶20），test 不参与自动抽取。也可明确选择重新划分全部图片，配置三者比例及 seed。同内容图片按组分配，避免产生新的划分泄漏。
- 每行标注 `类别 cx cy w h`（0~1 归一化），多边形（分割标注）也可以，训练检测模型时会换算成外接框。格式错误的行会删除并列出示例；类别编号超出类别名称数量会拒绝导入。
- 审计修复模式删除错误标注行并保留完整报告；严格模式拒绝错误标注或孤立标注。图片损坏、尺寸超过 4000 万像素、EXIF 旋转未规范化、重复路径及非法类别编号始终拒绝导入。
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

迁移 `20261023_0005` 新增 `training_datasets`、`training_runs`；`20261024_0008` 新增数据集系列、不可复用版本号、父版本、原包哈希和划分配置。历史记录回填为独立系列的 v1，保持原 UUID、规范化包哈希、统计与训练引用。历史缺失的原包不可追补，页面会明确显示。

有训练记录或后续版本引用的数据集不能删除；修改划分始终创建新版本。训练创建时固定版本、双哈希和划分配置，节点进度同步与发布模型均保留来源快照。升级需先迁移数据库，再更新 Backend/Frontend；runner 保留 test 路径的变动需重建训练镜像。生产发布前仍需备份数据库及整个 training-store（包括 `.source.zip` 和 `.index.json`）。
