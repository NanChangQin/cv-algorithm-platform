# 参数配置与生效回读

统一参数系统覆盖现有 YOLO 目标检测训练、独立验证与单张图片 PyTorch 推理。运行版本固定为 **Ultralytics 8.4.183**，并非按最新版网页猜测容器能力。

## 界面使用

- 训练中心、工作台、算法对比和媒体推理共用参数编辑器。支持常用/全部视图、中文与参数名搜索、分组、只看修改项和恢复默认。
- YAML / JSON 视图支持导入、导出和直接编辑。编辑后必须点“应用配置”；错误或未应用的配置会阻止任务提交。仅接受 JSON 可表达的值，保留数组、对象、null、false 和 0；不接受 YAML 自定义标签、重复键、别名和非有限数。
- 训练开放 69 项、独立验证 25 项，现有单图适配器开放 12 项。平台管理字段和待接入字段列出原因，API 同样明确拒绝。`device/data/model/project` 等通过相应选择器或任务目录配置。
- 单张图片推理参数沿用 `confidence/iou_threshold/max_detections/image_size`，适配器映射为上游的 `conf/iou/max_det/imgsz`。已发布的旧版本保留自己的 manifest 参数集合；使用新发布版本可获得扩展参数。

训练专家配置示例（省略字段使用平台原有 9 项默认值，其余使用固定版本的框架默认值）：

```yaml
epochs: 100
batch: 0.7             # 显存比例；-1 自动；正整数为固定 batch
imgsz: 640
optimizer: AdamW      # auto 会忽略手填 lr0
lr0: 0.001
classes: [0, 2]       # 数据集内的类别编号，null 为全部
freeze: [0, model.1]
amp: bf16
time: null
scale: [0.5, 1.5]
cache: disk           # 在本次任务可写副本中缓存，原数据集保持只读
```

是否支持 BF16、torch.compile 和 NMS-free 取决于模型与硬件，运行失败会回传错误。YOLO26 等模型可能调整或不使用特定设置；请结合生效快照及训练日志判断。多卡通过 API 选择，必须使用 GPU 数量正整数倍的 batch，并关闭 rect；本轮没有扩展多卡界面。

数据集可保留 train/val/test；训练中的验证 split 只接受 val，独立验证可选择三者。单张图片结果协议最多返回 10,000 个检测框。`save=false` 关闭常规 checkpoint 保存，但上游仍可能在最后一轮保存；`val=false` 关闭逐轮验证，但上游仍可能执行最终验证或提前停止检查。

`resume` 由平台的完整恢复点入口管理，不能在专家配置里直接填写。续训固定原训练计划，仅开放 batch/imgsz/workers/cache；需要更改训练计划时使用微调。详见 [模型来源与续训](TRAINING-SOURCES.md)。独立验证使用 val 参数契约，详见 [独立验证](VALIDATION.md)。

## 契约与兼容

共享 SDK 的 `runtime_contract.py` 是参数类型、默认值、范围、可用状态和别名的唯一实现。`ultralytics-8.4.183.json` 保存审计时提取的 113 项默认配置以及 API 参数目录；每个字段都有模式归属。对外接口：

```text
GET /api/v1/training/parameter-contract?mode=train
GET /api/v1/training/parameter-contract?mode=val
GET /training/parameter-contract?mode=train              # Manager，也支持 val/export
```

Backend 接口也可查询 predict/export/track/benchmark/tune 目录；train/val、FP32 ONNX export 和现有图片 predict 已接入，其他独立模式仍标为待接入。导出参数与同集验证限制见 [模型导出](EXPORTS.md)。目录完整不代表这些任务已实现。教师模型、QAT、自定义增强实例、更多导出后端与视频生命周期属于后续阶段。

Backend 与 Manager 用契约 SHA-256 检查一致性；Manager 检查训练镜像的 `cv.platform.parameter-contract` 标签，运行器检查契约 ID 和实际框架版本。必须一起更新 **Backend、Frontend、所有相关 Manager、训练镜像及 YOLO 推理镜像**。旧训练镜像没有标签会直接拒绝启动，避免忽略新增参数。修改契约语义时应升级契约 ID，并同步镜像标签。

原 manifest `schema_version: "1.0"` 及 primitive 参数继续支持。复杂参数使用 `schema_version: "1.1"`，格式如下；JSON Schema 使用 Draft 2020-12，不允许引用外部 schema：

```yaml
schema_version: '1.1'
# 其余 manifest 字段照常提供
parameters:
  classes:
    type: json
    default: null
    title: 类别筛选
    json_schema:
      anyOf:
        - type: 'null'
        - type: array
          items: {type: integer, minimum: 0}
          minItems: 1
          uniqueItems: true
```

## 回读与验证边界

训练记录的 `progress.configuration` 保留原始提交值、规范化值及提示；运行器启动后回传 effective 和 adjustments，包含实际 batch、优化器、初始及当前学习率。快照同时保存为训练产物 `parameter-resolution.json`，结束后随产物收回，可在详情页查看或下载。停止前尚未启动的任务保留提交快照。

YOLO 图片结果的可选 `configuration` 字段记录适配器收到的参数、上游名称映射和 predictor.args。平台在派发推理前已经补齐 manifest 默认值，因此这里的 submitted 是适配器输入。该字段可以在结果查看器或下载的结果 JSON 中查看。

测试按本地组件、真实 GPU、PostgreSQL 与生产部署分别记录；参数系统的早期本地测试不能替代后续验收。自定义权重、续训和独立验证的最新结果见 [训练生命周期验收](LIFECYCLE-VALIDATION.md)，数据版本管理见 [数据集验收](DATASET-VALIDATION.md)。

参考：[官方参数配置](https://docs.ultralytics.com/zh/usage/cfg/)、[训练](https://docs.ultralytics.com/zh/modes/train/)、[预测](https://docs.ultralytics.com/zh/modes/predict/)。升级时需重新核对实际 wheel 和参数目录，而非直接放开未知字段。
