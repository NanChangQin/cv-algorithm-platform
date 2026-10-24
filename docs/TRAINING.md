# 训练中心

在平台里完成“数据集版本 → 训练/微调/续训 → 独立验证 → 发布为推理模型 → 部署”。当前支持 YOLO 目标检测格式，训练框架固定为 Ultralytics 8.4.183（YOLO26 / YOLO11）。

## 使用流程

1. **资源库 → 训练数据集**：配置划分与校验模式后上传 ZIP。可以创建新的数据集，或选择已有版本上传新版本。详情页可切换版本、预览图片和标注、查看三划分统计与校验报告，下载原包和规范化包。详见 [数据集版本与校验](DATASETS.md)。
2. **新建训练**：先选视觉任务类型，再选算法和模型规格。当前开放目标检测；图像分类、实例分割、姿态估计、旋转框检测显示“待接入”且不可选。YOLO11 和 YOLO26 均提供 n / s / m / l / x 五种规格；默认算法为 YOLO11，切换算法后默认选 s。随后选择官方权重微调、从头训练或自定义权重来源，再选择数据集版本、训练服务器和 GPU，调整轮数、batch、图片尺寸、学习率等参数。支持上传 `.pt`、已有发布模型与历史 best/last 微调，详见 [模型来源与续训](TRAINING-SOURCES.md)。只列出检测到 GPU 的服务器；GPU 选“自动”时使用空闲显存最多的一张。
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

镜像基于 PyTorch 2.10 / CUDA 12.8，支持 RTX 50 系列。预训练权重（YOLO26n/s/m/l/x、YOLO11n/s/m/l/x）和绘图字体在构建时下载进镜像，训练容器默认**断网运行**（`ALGORITHM_MANAGER_TRAINING_NETWORK=none`）。构建机器访问不了 GitHub 时，先在能联网的机器上下载这些文件放进 `runtimes/yolo-trainer/assets/`（文件清单见该目录的 README），构建时直接使用。

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

## 首期界面与报告更新

- 训练中心提供新建训练和独立验证入口；任务摘要与可筛选、分页的任务列表移至左侧“模型训练 → 训练任务”，每页 6 项；日志和报告在任务详情中查看。新建训练使用独立页面，分为“算法与权重 → 数据与设备 → 参数与提交”三步。数据集导入和版本管理放在“资源库 → 训练数据集”，创建页仅选择数据版本和查看必要摘要。全部规格始终可选，资源提示按实际训练节点显示。短数值输入和全站共用参数表单使用紧凑布局。
- 自定义权重同时提交所选 `architecture`。来源已记录规格时服务端先校验；上传权重在隔离训练容器中校验目标检测结构及 scale，不匹配会明确失败，不会静默换模型。旧 API 调用允许不传该字段。
- 新训练镜像声明 `cv.platform.model-selection=v1`，使用规格校验需要同步更新 Manager 和训练镜像；旧的恢复 checkpoint 协议保持兼容。
- 正常训练结束后增加 `evaluation.json`，记录 best.pt 最终验证的整体及分类别指标、权重哈希和数据划分。它与 `results.csv` 中逐轮记录分开展示，避免把两者混作同一次评估。
- 训练详情提供“完整报告”，支持在线查看与 PDF 下载；曲线和评估图表支持单张 PNG 和批量 PNG ZIP；指标支持 CSV / JSON；日志可下载。
- 完整产物 ZIP 包含原始产物和 `report/` 下的 PDF、报告 JSON、轮次 CSV、类别 CSV、日志、图表包。页面、数据下载和 PDF 共享同一报告快照逻辑。
- 报告记录任务状态、算法规格、训练方式、权重来源与哈希、数据版本与划分、逐轮最佳/最终指标、最终模型评估、提交/规范化/生效参数。运行中、停止、失败和历史记录缺失信息均明确标注，不以零值填充缺失指标。
- PDF 使用后端镜像内的文泉驿中文字体；本地开发也可使用训练资源目录中的 `Arial.Unicode.ttf`。报告依赖 `reportlab`、`matplotlib`，安装后端依赖即可准备 Python 包。
- 当前首期为目标检测、单任务单 GPU 的页面入口。多任务类型、单任务多 GPU 交互、跨实验比较、模板和自动调参尚未纳入本轮。

主要接口（沿用项目权限隔离）：

- `GET /api/v1/training/runs/{id}/report`：在线报告快照。
- `GET /api/v1/training/runs/{id}/download/{kind}`：`report.pdf`、`report.json`、`metrics.csv`、`metrics.json`、`per-class.csv`、`per-class.json`、`logs.txt`、`figures.zip`。
- `GET /api/v1/training/runs/{id}/charts/{name}`：指标及损失曲线 PNG。
- `GET /api/v1/training/runs/{id}/figure?name=...`：已登记图片产物转换为 PNG。
- `GET /api/v1/training/runs/{id}/archive`：原始产物与报告的完整 ZIP。

日志下载包含平台已保留的日志；旧记录无法补回未保存的完整终端输出。历史记录缺少分类别指标时，可以从已有权重执行独立验证。

### 页面入口

- `/training`：新建训练和独立验证入口。
- `/training/tasks`：独立的“训练任务”子栏，提供任务概况、搜索、类型/状态筛选和分页列表。
- `/training/new`：三步创建训练；微调、续训、验证以及预选数据集继续支持原有查询参数。
- `/training/datasets`：训练数据集版本列表，点击“导入数据集”后展开上传和校验配置。
- `/training/datasets/{id}`：数据版本详情、标注预览及导入报告。
- `/training/{id}`：单个任务的进度、日志、结果和后续操作。

创建过程中需要补充数据时，可在新标签页打开数据集管理；导入后回到创建页点击“刷新数据集列表”，已选算法和参数会保留。原 `/training?dataset=...`、`?resume=...` 等书签会转到独立创建页面。


### 训练任务分类（2026-10-23）

训练中心与新建训练页使用一致的视觉任务分类，顺序为「任务类型 → 算法 → 规格 → 权重 → 数据与设备 → 参数」。目标检测支持 YOLO11 / YOLO26 五种规格；断点续训保留原任务的算法和规格。

训练任务列表分别提供任务类型、算法、执行方式和状态筛选。执行方式区分从头训练、权重微调、断点续训、独立验证；缺少来源信息的历史记录显示为“训练”，不推断其初始化方式。历史目标检测任务自动归入目标检测；自定义架构无法识别系列时归入“自定义模型”。各筛选可组合使用，修改条件后回到第一页。

创建接口接受 `task_type: object_detection`，旧客户端省略该字段仍可正常提交。其他视觉任务类型在 API 层返回 422，不能通过修改页面绕过限制。此次调整未开放其他任务类型的训练或改变现有权重、数据与参数契约。

验收：前端相关测试 28 项、后端相关测试 34 项通过；构建与 ESLint 通过。本机浏览器确认五类任务展示、四个待接入项禁用、YOLO26 五规格保留、组合筛选有效、未接入类型的直达链接被拦截，手机页面无横向溢出。

### 评测、样本审核与实验对比（2026-10-24）

- **评测中心** `/evaluations` 统一提供单模型完整验证和多模型对比入口。单模型验证沿用 YOLO 的 COCO mAP、分类别指标与混淆矩阵；多模型对比可以直接复用某个训练数据版本的 train/val/test 划分，冻结该版本的图片与标注。单次最多 2000 张图片且图片数 × 模型数不超过 2000。
- **错误样本分析** 位于检测评测报告中，按模型、误检 / 漏检 / 正确检测及类别过滤、分页。匹配规则为 IoU ≥ 0.5、同类别、预测置信度降序、一对一匹配；错分类同时计入误检和漏检。只统计成功完成的结果，固定阈值 P/R/F1 不等同于 COCO mAP。图片可叠加查看预测框和真实标注。
- **问题样本与审核** `/training/reviews` 可从评测样本、推理结果或历史任务收集。评测来源以真实标注初始化，普通推理来源以预测初始化；两者都先进入待审核状态。支持绘制、移动、缩放、修改坐标与类别、删除框，保存草稿后再明确审核通过。没有框的图片可审核为背景图片；不同浏览器同时编辑时使用版本号检测冲突。
- **生成数据版本**：勾选已审核样本，选择目标训练数据版本；所有类别必须已存在于目标数据集。相同内容的图片只在新版本中替换标注，并保留原划分；仅新图片按所选划分加入。原版本不变，审核记录保留发布时的标注与新版本引用。评测样本再用于改进模型时需确认不能继续把这些样本作为独立验收数据。生成版本后可直接进入新建训练。
- **实验对比**：在训练任务列表勾选 2–4 个实验，比较数据版本、参数差异、逐轮最佳与最终验证指标、分类别指标和叠加曲线，下载对比 CSV / 完整 JSON。设备、数据内容或验证参数不一致时显示提示，不直接给出模型排名；缺失指标显示“—”。
- **复用配置**：在任务详情或对比页点击“复用配置”，预填算法、规格、原初始化来源、数据版本、参数和可用设备。必须检查后手动提交，打开页面不会启动训练。复用断点续训配置时创建独立微调实验，不恢复优化器状态；需要接续原进度仍使用原续训入口。

审核接口需要开发者或管理员权限，并按项目隔离。审核记录存于 PostgreSQL `sample_reviews`，部署需迁移至 `20261028_0012`；内存模式仅用于开发测试。当前实现面向本机单后端服务，不提供跨多个后端进程的发布事务保证。旧 `/training/datasets/{id}/feedback` 接口为兼容保留，新网页流程统一经过人工审核。

批量推理遇到模型实例 HTTP 429 繁忙响应时，在推理时限内退避重试；不自动重放超时或其他服务器错误，避免不确定的重复执行。

本轮验收：后端 250 项、前端 126 项测试通过，Ruff（本轮后端文件）、ESLint、类型检查与生产构建通过。数据库迁移在独立库执行升级、回退与再升级后，备份本机数据库并应用迁移。两个 YOLO26 CPU 测试版本各完成 4 张 COCO8 图片的真实推理，验证错误筛选、审核状态、重复收集和不可变数据版本；网页实测绘制、移动、坐标编辑、删除、保存再加载、审核及生成 v3 数据。实验对比、CSV 下载、复用配置不自动提交，以及 390px 窄屏无页面横向溢出均通过。

GPU 复验尚未完成：本机 `nvidia-smi` 报 `Driver/library version mismatch`，加载的内核模块为 595.91.07，NVML 库为 595.99；本次没有重启主机或改动显卡驱动。GPU 恢复后需补验“审核后的新版本直接训练”。CPU 测试模型已停用并停止实例，原 YOLO11/YOLO26 模型保留；本机服务仍不随开机启动。验收数据和截图保存在被 Git 忽略的 `runtime/workflow-improvements/`。


## Faster R-CNN 训练与推理一致性（2026-10-24）

算法名称为 **Faster R-CNN ResNet50-FPN**，对应 `fasterrcnn_resnet50_fpn`，不是 Fast R-CNN，也不是 FPN V2。新建训练先选“目标检测 → Faster R-CNN”，再选权重、数据版本、执行设备和参数。已有 YOLO11 / YOLO26 功能保留。

支持官方 COCO 权重微调、从头训练、平台自定义权重、历史训练微调、完整断点续训、独立验证、发布到模型库及推理中心。Faster R-CNN 当前提供这一种结构，不套用 YOLO 的 n/s/m/l/x 规格或数据增强参数；暂不开放 ONNX 导出。

- 设备：单 GPU 或显式 CPU。CPU 可用于无可用 GPU 的节点；不会在 GPU 请求失败时静默切换 CPU。CPU 与 GPU 编号不可混用。
- 数据：沿用平台的目标检测数据版本和 YOLO 标注格式；加载时将数据集 0 起始类别编号转换为模型的 1 起始编号，模型 0 专用于背景。验证/续训要求权重类别名称及顺序完全相同；微调按类别名重映射分类头和回归头。
- 参数：SGD / AdamW、学习率、动量、权重衰减、StepLR 步长/衰减、可训练骨干层数、水平翻转、随机种子及加载线程数。`imgsz` 为短边，`max_size` 为长边上限，两者须协调设置。从头训练要求骨干 5 个阶段均可训练。CPU 小规模流程验证可用 batch=2、imgsz=128、max_size=192、workers=0；这不是正式精度训练推荐配置。
- 权重：训练产生 `weights/best.pt`、`weights/last.pt`，可恢复时包含 `weights/resume.pt`。上传 .pt / .pth 需为平台格式，包含 architecture、class_names 和 state_dict；恢复文件还保存 optimizer、scheduler、随机状态、历史指标及数据哈希。任意外部裸权重不能直接声明兼容。
- 续训：保留原数据版本、类别、总轮次及优化器计划，只允许调整 batch、workers、device、plots；不能把微调当作完整恢复。
- 发布：自动写入 `model.pth`、`model-config.yaml` 和 `manifest.yaml`，包含模型结构、类别映射、图像预处理及来源信息。训练和推理共用 SDK 模型构造代码、torch 2.10.0 / torchvision 0.25.0。推理加载严格校验结构与类别顺序，按类别进行 NMS，并使用已发布模型的 ID/版本返回结果。
- 结果：实时轮次/进度、分类/框回归/RPN/objectness 损失、COCO mAP50 和 mAP50–95、分类别 AP、混淆矩阵；支持网页、CSV/JSON、PNG 图表、PDF 报告及完整产物包。P/R 使用固定 confidence=0.25、IoU=0.5，不能直接视为 YOLO 最大 F1 阈值下的 P/R；跨算法对比需同时核对数据版本和评估口径。

### 构建依赖

先准备已有 YOLO 训练基础镜像，再依次构建 Faster R-CNN 训练镜像、推理镜像。官方权重在构建时放入镜像，训练阶段不需要联网下载：

```bash
mkdir -p runtimes/faster-rcnn-trainer/assets
curl -fL --retry 3 https://download.pytorch.org/models/fasterrcnn_resnet50_fpn_coco-258fb6c6.pth -o runtimes/faster-rcnn-trainer/assets/fasterrcnn_resnet50_fpn.pt
# Dockerfile 校验官方文件 SHA256 前缀 258fb6c6。
docker compose -f compose.yaml -f compose.database.yaml -f compose.local.yaml --profile database --profile algorithm-images build faster-rcnn-trainer
docker compose -f compose.yaml -f compose.database.yaml -f compose.local.yaml --profile database --profile algorithm-images build faster-rcnn
```

本机继续使用 conda `cv-platform` 环境及手动启停的 Docker 服务，不启用开机自启动。训练容器中断后，已完整完成轮次的恢复文件可用于新建续训任务；首轮 checkpoint 生成前停止则不能恢复。


验收结果：后端完整回归 251 项、训练节点 74 项、前端 127 项通过，另有推理镜像内 2 项结构/类别/NMS 契约测试通过；Ruff、ESLint、类型检查和生产构建通过。CPU 实测官方 COCO8 单轮微调、zebra/elephant 两类别从头训练、停止后恢复至完整 4 轮、发布权重独立验证（与训练最终评估指标完全一致）、训练模型发布后真实图片推理及模型 ID/版本核对，报告和图表下载通过。小规模验收只验证功能与兼容性，不代表正式精度效果。

浏览器验证了算法与参数切换、CPU 配置、配置复用、任务筛选、报告、推理中心模型选择及 390px 窄屏布局，无脚本错误或页面横向溢出。截图、报告与运行记录位于忽略目录 `runtime/faster-rcnn-training/`。同时修复已删除的失败推理容器仍被节点缓存引用、无法重建的问题。

本机服务已更新，docker.service、docker.socket、containerd.service 仍为 disabled，平台容器 restart=no。GPU 实机验收仍因 NVIDIA 内核模块与 NVML 版本不一致而待补；没有重启主机或改动显卡驱动。
