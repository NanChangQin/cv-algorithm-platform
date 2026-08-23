# 运行监控

Backend 在 `/metrics` 暴露 Prometheus 文本指标，包括：

- HTTP 请求量、状态码和耗时直方图；
- 各状态推理任务数量；
- 推理与构建的排队数、执行数；
- 平均模型推理耗时；
- 当前算法运行实例数。

启动监控组合：

```bash
export CV_PLATFORM_GRAFANA_ADMIN_PASSWORD='<设置独立密码>'
docker compose \
  -f compose.yaml \
  -f compose.database.yaml \
  -f compose.monitoring.yaml \
  --profile database up -d
```

Prometheus 和 Grafana 默认只监听服务器回环地址，分别为 `127.0.0.1:9090` 和 `127.0.0.1:3000`。需要从其他电脑访问时，优先使用 SSH 端口转发：

```bash
ssh -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090 192.168.0.61
```

Grafana 会自动创建 Prometheus 数据源。建议先建立任务吞吐、失败率、P95 API 延迟、队列积压和平均推理耗时五个面板，再根据真实运行数据配置告警阈值。
