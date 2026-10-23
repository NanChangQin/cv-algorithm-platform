# 模型来源、续训与独立验证验收

日期：2026-10-23。范围：自定义 `.pt`、已发布/历史模型微调、完整 checkpoint 续训、独立检测验证。当前是开发及隔离验收结果，**尚未生产部署，也未完成新版页面的真实浏览器验收**。

## 自动化回归

| 层级 | 结果 | 验证内容 |
| --- | --- | --- |
| Backend + PostgreSQL 17 | 175 passed | 全量接口与持久化回归，包括项目隔离、来源副本、哈希、续训约束、独立验证和历史数据集版本功能 |
| Backend 来源/独立验证专项，本地 memory | 25 passed | 删除原来源后仍能启动、参数/数据锁定、best 副本、缺少 split、报告下载与来源快照 |
| Algorithm Manager | 58 passed | 协议匹配、权重传输/校验、只读输入挂载、相同 SHA 的双输入、val 配置、产物范围 |
| SDK 参数 + runner | 40 passed | 全参数转发、快照回读、完整 checkpoint 防剥离、中断复制保留旧代、optimizer 固定、路径重映射及输入哈希 |
| Frontend | 23 文件 / 99 passed | 权重上传与来源选择、续训锁定、val 契约提交、报告显示及已有页面回归 |
| 前端构建与静态检查 | 通过 | `vue-tsc -b`、Vite build、ESLint；本功能 Backend/Manager/runner Python 改动的 Ruff 检查 |
| nginx 1.28 | `nginx -t` 通过 | 2 GiB 权重上传专用 location；只读挂载新配置的独立临时容器 |

Python 测试仅有 Starlette TestClient 的依赖弃用提示，没有失败。Frontend 完整构建包含类型检查；最后的页面修改仅修正缩进，ESLint 已再次通过。

PostgreSQL 使用独立 internal Docker network、独立数据库和 tmpfs；测试容器显式设置数据库 URL、storage 与 package 目录，不挂载生产数据。迁移检查包含历史 `0007 → 0008 → 0007` 两轮以及 `base → head → base → head`，历史训练、参数、数据 UUID 与哈希保持不变。测试结束已清理该网络与 PostgreSQL 容器。

本功能没有新增迁移；数据版本管理的 `20261024_0008` 仍需随先前改动一起发布。

## 真实 GPU

在 RTX 2080 Ti 上使用 PyTorch `2.10.0+cu128`、Ultralytics `8.4.183` 和固定训练镜像，挂载本次 runner/SDK 源码执行 [verify_training_resume.py](../scripts/verify_training_resume.py)。容器断网、非 root、cap-drop ALL、独立 `/tmp` 目录，无平台凭据、生产数据库或生产数据卷。

测试流程：

1. 创建 8 张 train、4 张 val、4 张 test 合成检测图片。
2. 第 1 轮完整 checkpoint 提交后执行 `os._exit(137)`，模拟进程硬中断。
3. 从该 checkpoint 真正恢复，继续第 2、3 轮。
4. 从同一来源新建一轮微调，确认没有恢复旧训练状态。
5. 用恢复后训练权重独立执行 `split=test` 验证，检查 JSON、分类别实例数与混淆矩阵。

最终第 r6 次证据：

- 各阶段退出码：中断 **137**，续训/微调/验证均 **0**。
- 恢复 `start_epoch=1`，下一轮为 2；optimizer 状态项 **255**，EMA updates **1**。
- 实际 optimizer **SGD**，初始学习率 **0.01**；AMP scaler 恢复为 scale **128**、growth tracker **1**。
- 历史 CSV 轮次为 **[1, 2, 3]**；微调只有 **[1]**。
- 普通 last.pt 已被框架剥离 optimizer，单独完整 checkpoint 仍保留 optimizer；达到原计划总轮数后标记不可续训。
- 独立 test 报告包含 4 个实例、PR 图及矩阵 `[[4, 306], [0, 0]]`，且不产生训练 weights 目录。

极小 fixture 在默认 AMP scale=65536 时可能第一轮溢出，无法验证非空 optimizer 状态。因此**仅测试 harness**在第一阶段把初始 scale 设为 128；正式 runner 没有改变 scaler 默认行为。数据集哈希 `aaaa...` 是该隔离 runner 测试显式提供的哨兵值；真实平台的哈希生成与传输由 API/Manager 测试覆盖。

这是运行正确性验证，合成数据的 mAP 和速度不能作为业务模型精度或部署性能结论。

## 证据与复现

- 可追踪的机器可读摘要：[gpu-lifecycle-20261023.json](research/gpu-lifecycle-20261023.json)。
- 本地完整日志、恢复报告、验证报告、PR/矩阵图片：`build/lifecycle-20261023/gpu-r6/`。
- 隔离数据库脚本/源码包/日志：`build/lifecycle-20261023/verify_postgres.py`、`postgres-source.tar.gz`、`postgres-regression.log`。
- nginx 日志及源码/证据 SHA-256：`build/lifecycle-20261023/nginx-check.log`、`SHA256.json`。
- 远端 GPU 原始证据：`/tmp/cv-model-sources-20261023/evidence-r6/`。`build/` 和 `/tmp` 不属于长期发布存储，正式归档时应另外保留；摘要和复现 harness 已放入源码目录。

在具有同版本框架和预置 YOLO11n 权重的隔离 GPU 容器中复现：

```bash
# 将仓库只读挂载至 /tests，新建空目录挂载至 /evidence。
# PYTHONPATH=/tests/packages/algorithm-sdk/src，HOME=/evidence。
python /tests/scripts/verify_training_resume.py /evidence
```

每次使用新的空 evidence 目录，避免已有 CSV 与缓存干扰。

## 尚待上线验收

新版镜像构建与各节点协议升级、生产备份/迁移/部署，以及新版浏览器从上传来源到节点派发再收回报告的整链路仍需执行。不能把隔离 runner 成功等同于生产平台已升级。本轮未重启生产服务，未提交或推送代码。

适用边界见 [模型来源与续训](TRAINING-SOURCES.md) 与 [独立验证](VALIDATION.md)。后续独立阶段是 ONNX 等格式导出、导出后验证和部署运行配置。
