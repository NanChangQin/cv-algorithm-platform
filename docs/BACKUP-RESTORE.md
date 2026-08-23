# 服务器备份与恢复

平台的完整恢复点由 PostgreSQL 与三个受控文件目录共同组成：素材数据、算法包、模型权重。仅备份数据库不能恢复可运行的平台。

## 创建备份

在服务器项目目录执行：

```bash
chmod +x scripts/backup-server.sh scripts/restore-server.sh
./scripts/backup-server.sh
```

默认写入 `backups/<UTC 时间>/`，包括 `postgres.dump`、三个压缩包、清单和 SHA-256 校验文件。可以通过 `CV_PLATFORM_BACKUP_ROOT` 指定其他磁盘。

建议用 cron 每日执行，并由外部存储同步整个备份目录。脚本不会自动删除旧备份，避免保留策略配置错误造成不可恢复的数据删除。

## 恢复

恢复会短暂停止平台服务，并清理数据库中的现有业务对象。必须显式传入确认参数：

```bash
./scripts/restore-server.sh backups/20260823T120000Z --confirm-restore
```

恢复前会验证所有文件校验和。现有文件目录不会直接删除，而是改名为 `.pre-restore-<时间>`，确认恢复成功后再人工清理。

## 验收

恢复完成后检查：

```bash
docker compose -f compose.yaml -f compose.database.yaml --profile database ps
curl -fsS http://127.0.0.1:8081/api/v1/algorithms
```

至少每月在隔离环境完成一次恢复演练，确认数据库记录、素材文件、算法包和模型权重能够对应。
