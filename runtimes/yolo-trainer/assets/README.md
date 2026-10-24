# 离线构建用的预训练权重和字体

构建训练镜像时会把本目录里的文件复制进镜像，缺少的再从 GitHub 下载。
构建机器无法访问 GitHub 时，先在能联网的机器上下载下列文件放到这里：

- YOLO11 权重：`yolo11n.pt`、`yolo11s.pt`、`yolo11m.pt`、`yolo11l.pt`、`yolo11x.pt`
  （https://github.com/ultralytics/assets/releases/download/v8.3.0/<文件名>）
- YOLO26 权重：`yolo26n.pt`、`yolo26s.pt`、`yolo26m.pt`、`yolo26l.pt`、`yolo26x.pt`
  （https://github.com/ultralytics/assets/releases/download/v8.4.0/<文件名>）
- 绘图字体：`Arial.ttf`、`Arial.Unicode.ttf`（https://ultralytics.com/assets/<文件名>）

本目录下的 `.pt`、`.ttf` 文件不会提交到仓库。
