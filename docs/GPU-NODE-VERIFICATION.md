# 双服务器 GPU 联调记录

日期：2026-10-16。

## 已部署节点

| 节点 | 地址 | GPU | 部署内容 |
|---|---|---|---|
| local | 192.168.8.118:8081 | RTX 2080 Ti / 11 GB | 主平台、数据库、Worker、Manager |
| dell-gpu | 192.168.8.135:8010 | RTX 5070 Ti / 约 16 GB | 独立 Manager、Faster R-CNN 算法容器 |

Dell 节点目录：`/home/dell/docker/cv-platform-node`。主平台目录：`/home/f512/docker/cv-algorithm-platform`。

Dell 已安装 NVIDIA Container Toolkit，Docker 配置仅热重载，原有容器继续运行。Manager 使用 NVIDIA runtime 模式，把选定索引写入 `NVIDIA_VISIBLE_DEVICES`。

Manager、CUDA GPU 检测镜像、Docker CLI 插件和模型文件通过局域网复制。算法镜像复制 Dell 既有的 `slj_slam` Conda 环境构建，使用 PyTorch 2.7.1 / CUDA 12.8；仅在镜像内补充少量 FastAPI/uvicorn 依赖，没有修改原 Conda 环境。

## 验证结果

- Python 回归测试：55 项通过；前端交互测试：3 项通过；类型检查与生产构建通过。
- 数据库迁移：`20261016_0002`；使用临时表验证请求节点/GPU、实际节点/GPU 的新增、读取、更新，不写业务测试数据。
- CUDA 实际运算：GPU 0 的矩阵乘法与 torchvision CUDA NMS 成功。
- 从前端选择 Dell / GPU 0 提交，任务 `a08816fa-a448-4b2c-9dac-50beee883727` 完成，返回 1 个检测结果；推理约 781.8 ms，实际节点 `dell-gpu`。
- 再次指定 Dell / GPU 0，任务 `3cbe40a4-e7ab-4dd4-b50a-5ef995cc5555` 完成，复用同一容器；推理约 135.9 ms。
- 指定主服务器 / GPU 0，任务 `87739db2-c641-4a50-b235-bedcc081eaf6` 完成；实际节点 `local`，推理约 883.9 ms。
- 以上耗时仅为一张样本图片的联调数据，不是基准测试。

## 备份与范围

升级前备份：主平台的 `backups/20261016T014723Z/`，包含 PostgreSQL、输入文件、算法包及模型；旧服务镜像保留 `pre-multinode-20261016` 标签。旧源码包保存在 `/home/f512/docker/cv-platform-release-multinode-20261016/pre-update-source.tar.gz`。

当前已为 Faster R-CNN 准备远端镜像和模型；后续新增算法仍需向目标执行节点分发其镜像与权重，当前没有自动镜像/模型分发。图片输入自动按需传输，任务完成后远端临时输入清理。额外文件型输出的跨节点访问不在本次覆盖范围内。
