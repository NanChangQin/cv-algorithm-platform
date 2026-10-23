# 数据集阶段验收记录 · 2026-10-22

本轮完成 P1 的数据集管理部分。代码与数据库迁移已实现、验证；未部署到生产服务，未新增真实 GPU 训练任务。

## 功能证据

| 项目 | 结果 |
| --- | --- |
| train / val / test | 三划分均写入规范化 ZIP；缺 val 时只从 train 抽取，test 不充当 val |
| 可复现划分 | 比例、seed、模式可配置；ZIP 顺序/包装层变化后规范化哈希相同；seed 改变后划分发生变化 |
| 校验与泄漏 | 严格拒绝/审计修复、损坏图片和重复路径拒绝、原始字节完全重复及跨划分泄漏报告 |
| 版本 | 新包或重新划分生成新版本；并发版本号唯一；删除最新版本后不复用编号 |
| 追溯 | 训练持久化数据版本/双哈希/划分配置快照；节点进度同步及模型发布保留快照 |
| 权限 | 详情、图片、预览、下载、报告、派生版本均按项目隔离 |
| 历史兼容 | 迁移保留原 ID、统计、哈希和训练引用；旧包可只读预览；缺失原包明确提示 |

## 自动化

- 后端完整套件：内存 **150 passed**；独立 PostgreSQL 17 **150 passed**。
- 前端完整套件 **95 passed**；补充模型来源展示后，相关三文件 **19 passed**；ESLint、TypeScript 与生产构建通过。
- runner 参数与可选 test 路径测试 **5 passed**；发布来源快照补强断言与 runner 联合复测 **15 passed**。
- 后端训练模块 Ruff、迁移链静态检查及 `git diff --check` 通过。
- 真实 PostgreSQL 迁移：0007 插入历史数据集及 completed 训练 → 0008 → 0007 → 0008，重复验证旧 ID/哈希/统计/引用；另执行完整 upgrade head / downgrade base / upgrade head。

PostgreSQL 验收使用独立 internal 网络、临时容器和 tmpfs 数据目录，连接凭据为专用测试值。没有连接生产数据库；测试结束后已确认临时容器和网络清理完毕。

脚本与原始输出保存在工作区 `build/datasets-20261022/verify_remote.py`、`postgres-verification.log`。该目录中的 source.tar.gz 是后端测试源码包，不是生产发布包。

## 浏览器

独立本地开发服务（内存库）上传 24 张合成标注图：v1 为 train=16 / val=4 / test=4。通过页面设置 60% / 20% / 20%、seed=42，生成 v2，实际计数 14 / 5 / 5。

已验证版本切换、test 筛选、图片预览、框显示开关；返回 v1 后原划分、原哈希均保持不变。独立 HTTP 回读确认 v1/v2 的原包哈希一致，下载的规范化包均匹配登记哈希。详情页截图与回读记录保存在 `build/datasets-20261022/preview-browser.png` 和 `ui-versions-verified.json`。

![本地浏览器的独立测试集标注预览](../build/datasets-20261022/preview-browser.png)

## 部署边界

本轮没有更新线上 Backend/Frontend/训练镜像，也没有迁移生产数据库。上线时先备份数据库及 training-store，再执行 0008、更新服务，并重建包含 `prepare_data()` test 路径的训练镜像。未用本轮结果替代真实 GPU 训练与生产客户端验收。
