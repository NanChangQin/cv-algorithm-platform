"""One snapshot for online reports, metrics downloads, figures and PDF exports."""

from __future__ import annotations

import csv
import io
import json
import math
import threading
import zipfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from PIL import Image as PILImage

from ...core.errors import ApplicationError
from .domain import TRAINING_MODELS, TrainingStatus

METRICS = {
    "metrics/precision(B)": "Precision",
    "metrics/recall(B)": "Recall",
    "metrics/mAP50(B)": "mAP50",
    "metrics/mAP50-95(B)": "mAP50-95",
}
STATUS = {
    "queued": "排队中",
    "preparing": "准备中",
    "running": "训练中",
    "stopping": "正在停止",
    "completed": "已完成",
    "stopped": "已停止",
    "failed": "失败",
}
_RENDER_LOCK = threading.RLock()


def clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    return value


def json_bytes(value) -> bytes:
    return json.dumps(clean(value), ensure_ascii=False, indent=2, default=str).encode("utf-8")


def csv_bytes(rows: list[dict]) -> bytes:
    stream = io.StringIO(newline="")
    fields = list(dict.fromkeys(key for row in rows for key in row))
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        # Spreadsheet formula protection for user-controlled category names.
        writer.writerow(
            {
                key: "'" + value
                if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r"))
                else value
                for key, value in row.items()
            }
        )
    return stream.getvalue().encode("utf-8-sig")


def snapshot(service, run) -> dict:
    progress = run.progress or {}
    configuration = progress.get("configuration", {})
    if "parameter-resolution.json" in run.artifacts:
        configuration = json.loads(
            service.artifact(run.id, "parameter-resolution.json").read_text()
        )
    evaluation = service.evaluation_report(run.id) if "evaluation.json" in run.artifacts else None
    architecture = progress.get("architecture") or run.model
    model = next((item for item in TRAINING_MODELS if item.key == architecture), None)
    rows = clean(run.metrics)
    scored = [row for row in rows if row.get("metrics/mAP50-95(B)") is not None]
    best = (
        max(scored, key=lambda row: (row["metrics/mAP50-95(B)"], row.get("metrics/mAP50(B)") or 0))
        if scored
        else None
    )
    source = {**progress.get("source", {"label": run.model})}
    if not source.get("sha256") and configuration.get("initial_weights"):
        source["sha256"] = configuration["initial_weights"]["sha256"]
    notes = []
    if model and model.algorithm == "fasterrcnn":
        notes.append(
            "Faster R-CNN：P/R 使用置信度 0.25、IoU 0.5；COCO mAP 使用 IoU 0.50:0.95、maxDets=100。"
            "请勿将不同工作点的 P/R 直接排名。"
        )
    if run.parameters.get("pretrained") is False:
        notes.append(
            "从头训练使用随机初始化；来源文件及其哈希用于追溯模型结构，不表示加载了预训练参数。"
        )
    if run.status != TrainingStatus.COMPLETED:
        notes.append("这是当前任务快照，任务未成功完成；已有指标及产物可能不完整。")
    if not configuration.get("effective"):
        notes.append("尚无实际生效参数快照；提交参数不代表运行器最终采用的配置。")
    if not evaluation:
        notes.append("尚无分类别评估数据。历史训练可使用独立验证补充。")
    if not rows:
        notes.append("尚无轮次指标，空缺不表示零分。")
    notes.append("曲线和最佳轮次来自逐轮验证；最佳权重最终评估单独列出。指标为 0-1 原始数值。")
    return clean(
        {
            "schema_version": 1,
            "id": str(run.id),
            "name": run.name,
            "status": run.status.value,
            "status_label": STATUS[run.status.value],
            "snapshot_at": run.updated_at.isoformat(),
            "created_at": run.created_at.isoformat(),
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "created_by": run.created_by,
            "operation": progress.get("operation", "train"),
            "algorithm": model.algorithm if model else "历史 / 自定义模型",
            "scale": model.scale if model else None,
            "architecture": architecture,
            "task": "detect",
            "source": source,
            "training_mode": "resume"
            if progress.get("source", {}).get("mode") == "resume"
            else "scratch"
            if run.parameters.get("pretrained") is False
            else "finetune",
            "node_id": run.node_id,
            "gpu_device_ids": list(run.gpu_device_ids),
            "dataset": progress.get("dataset", {"id": str(run.dataset_id)}),
            "parameters": run.parameters,
            "configuration": configuration,
            "best": best,
            "last": rows[-1] if rows else None,
            "metrics": rows,
            "evaluation": evaluation,
            "artifacts": run.artifacts,
            "figures": [
                name
                for name in run.artifacts
                if Path(name).suffix.lower() in {".png", ".jpg", ".jpeg"}
            ],
            "error": run.error,
            "notes": notes,
            "logs_note": "下载包含平台已保留的日志；运行中日志为节点当前可读取的内容。",
        }
    )


def chart_groups(report: dict) -> dict:
    rows = report["metrics"]
    groups = {}
    if any(any(row.get(key) is not None for key in METRICS) for row in rows):
        groups["metrics"] = ("Validation metrics", METRICS)
    kinds = sorted(
        {
            key.split("/")[1][:-5]
            for row in rows
            for key in row
            if key.startswith(("train/", "val/")) and key.endswith("_loss")
        }
    )
    for kind in kinds:
        groups[f"loss-{kind}"] = (
            f"{kind} loss",
            {
                f"train/{kind}_loss": "Train",
                f"val/{kind}_loss": "Validation",
            },
        )
    return groups


def chart_png(report: dict, name: str) -> bytes:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.ticker import MaxNLocator

    group = chart_groups(report).get(name)
    if group is None:
        raise ApplicationError("CHART_NOT_FOUND", "该图表尚无可用数据", 404)
    title, series = group
    with _RENDER_LOCK:
        figure = Figure(figsize=(9, 4), dpi=150, layout="constrained")
        FigureCanvasAgg(figure)
        axes = figure.subplots()
        for key, label in series.items():
            points = [
                (row["epoch"], row[key])
                for row in report["metrics"]
                if row.get("epoch") is not None and row.get(key) is not None
            ]
            if points:
                axes.plot(
                    *zip(*points, strict=True),
                    label=label,
                    linewidth=1.8,
                    marker="." if len(points) < 20 else None,
                )
        axes.set(title=title, xlabel="Epoch", ylabel="Value (0-1)" if name == "metrics" else "Loss")
        if name == "metrics":
            axes.set_ylim(0, 1)
        axes.xaxis.set_major_locator(MaxNLocator(integer=True))
        epochs = [row["epoch"] for row in report["metrics"] if row.get("epoch") is not None]
        if epochs and min(epochs) == max(epochs):
            axes.set_xlim(epochs[0] - 0.5, epochs[0] + 0.5)
            axes.set_xticks([epochs[0]])
        axes.grid(alpha=0.18)
        axes.legend(loc="best")
        output = io.BytesIO()
        figure.savefig(output, format="png")
        figure.clear()
        return output.getvalue()


def figure_png(service, run, name: str) -> bytes:
    if Path(name).suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise ApplicationError("CHART_NOT_FOUND", "不支持的图表文件", 404)
    path = service.artifact(run.id, name)
    output = io.BytesIO()
    with PILImage.open(path) as image:
        image.convert("RGB").save(output, format="PNG")
    return output.getvalue()


def figures_zip(service, run, report) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in chart_groups(report):
            archive.writestr(f"curves/{name}.png", chart_png(report, name))
        for index, name in enumerate(report["figures"]):
            archive.writestr(
                f"figures/{index:03d}-{Path(name).stem}.png", figure_png(service, run, name)
            )
        archive.writestr(
            "manifest.json",
            json_bytes(
                {
                    "run_id": str(run.id),
                    "snapshot_at": report["snapshot_at"],
                    "notes": report["notes"],
                }
            ),
        )
    return output.getvalue()


def pdf_report(service, run, report: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        Image,
        KeepTogether,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    with _RENDER_LOCK:
        if "CVChinese" not in pdfmetrics.getRegisteredFontNames():
            candidates = [
                Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
                Path(__file__).resolve().parents[5]
                / "runtimes/yolo-trainer/assets/Arial.Unicode.ttf",
                Path("../runtimes/yolo-trainer/assets/Arial.Unicode.ttf"),
            ]
            font = next((path for path in candidates if path.is_file()), None)
            if font is None:
                raise ApplicationError(
                    "REPORT_FONT_MISSING", "缺少中文报告字体，请更新后端镜像", 503
                )
            pdfmetrics.registerFont(TTFont("CVChinese", str(font)))
        body = ParagraphStyle(
            "body",
            fontName="CVChinese",
            fontSize=9,
            leading=14,
            wordWrap="CJK",
            spaceAfter=5,
            textColor=colors.HexColor("#243247"),
        )
        heading = ParagraphStyle(
            "heading",
            parent=body,
            fontSize=14,
            leading=21,
            spaceBefore=14,
            spaceAfter=9,
            keepWithNext=True,
        )
        title = ParagraphStyle("title", parent=body, fontSize=23, leading=32, spaceAfter=16)

        def para(value, style=body):
            return Paragraph(escape(str(value)).replace("\n", "<br/>"), style)

        def time_label(value):
            return (
                datetime.fromisoformat(value)
                .astimezone(ZoneInfo("Asia/Shanghai"))
                .strftime("%Y-%m-%d %H:%M:%S +08:00")
                if value
                else "暂无"
            )

        story = [
            para("模型训练报告", title),
            para(report["name"], heading),
            para(f"{report['status_label']} | 快照时间 {time_label(report['snapshot_at'])}"),
            para(f"训练 ID：{run.id}"),
        ]
        for note in report["notes"]:
            story.append(para(note))

        def section(name):
            story.append(para(name, heading))

        def pair(label, value):
            story.append(para(f"{label}：{value if value is not None else '暂无'}"))

        algorithm_label = (
            "Faster R-CNN" if report["algorithm"] == "fasterrcnn"
            else report["algorithm"].upper()
        )
        section("算法、数据与运行信息")
        for label, value in (
            ("算法 / 规格", f"{algorithm_label} / {report['scale'] or '未记录'}"),
            (
                "训练方式",
                "独立验证"
                if report["operation"] == "val"
                else {"resume": "断点续训", "scratch": "从头训练", "finetune": "权重微调"}[
                    report["training_mode"]
                ],
            ),
            ("初始权重", report["source"].get("label")),
            ("初始权重 SHA256", report["source"].get("sha256")),
            (
                "训练设备",
                f"{run.node_id} / "
                + (
                    "CPU"
                    if run.parameters.get("device") == "cpu"
                    else f"GPU {', '.join(run.gpu_device_ids)}"
                ),
            ),
            (
                "创建 / 开始 / 结束",
                f"{time_label(report['created_at'])} / {time_label(report['started_at'])} / "
                f"{time_label(report['finished_at'])}",
            ),
            (
                "数据集",
                f"{report['dataset'].get('name', '未记录')} / "
                f"v{report['dataset'].get('version', '未记录')}",
            ),
            ("数据版本 ID", report["dataset"].get("id")),
            ("数据内容 SHA256", report["dataset"].get("sha256")),
            ("原始数据 SHA256", report["dataset"].get("source_sha256")),
            (
                "数据划分",
                " / ".join(
                    f"{key}: {value.get('images', 0)} 张 / {value.get('boxes', 0)} 标注"
                    for key, value in report["dataset"].get("splits", {}).items()
                ),
            ),
            ("错误信息", run.error),
        ):
            pair(label, value)
        section("逐轮验证的最佳指标与最终轮次")
        for label, row in (("最佳轮次", report["best"]), ("最终轮次", report["last"])):
            pair(label, f"第 {int(row.get('epoch', 0))} 轮" if row else None)
            if row:
                for key, label in METRICS.items():
                    pair(label, row.get(key))
        section("训练过程曲线")
        for name in chart_groups(report):
            story.append(Image(io.BytesIO(chart_png(report, name)), width=490, height=218))
        evaluation = report["evaluation"]
        if evaluation:
            section("模型评估与分类别指标")
            pair(
                "评估对象 / 数据划分",
                f"{evaluation.get('checkpoint', '指定权重')} / {evaluation.get('split')}",
            )
            pair("评估权重 SHA256", evaluation.get("weight_sha256"))
            pair("整体指标", json.dumps(evaluation.get("metrics", {}), ensure_ascii=False))
            table_rows = [
                [para(item) for item in ["类别", "实例", "P", "R", "F1", "mAP50", "mAP50-95"]]
            ]
            for item in evaluation.get("per_class", []):
                table_rows.append(
                    [
                        para(item["name"]),
                        para(item["instances"]),
                        *[
                            para(f"{item[key]:.4f}" if item.get(key) is not None else "暂无")
                            for key in ("precision", "recall", "f1", "map50", "map50_95")
                        ],
                    ]
                )
            table = Table(table_rows, colWidths=[130, 35, 60, 60, 60, 65, 80], repeatRows=1)
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef3fa")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#dbe2eb")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            story.append(table)
        if report["figures"]:
            story.append(PageBreak())
        section("评估图表与预测样例")
        # Include every generated figure; CSV contains the complete epoch table.
        for name in report["figures"]:
            data = figure_png(service, run, name)
            with PILImage.open(io.BytesIO(data)) as image:
                width, height = image.size
            ratio = min(490 / width, 420 / height)
            story.append(
                KeepTogether(
                    [
                        para(name),
                        Image(io.BytesIO(data), width=width * ratio, height=height * ratio),
                        Spacer(1, 8),
                    ]
                )
            )
        story.append(PageBreak())
        section("提交参数与实际生效配置")
        configuration = report["configuration"]
        for label, value in (
            ("框架版本", configuration.get("framework_version")),
            ("参数契约", configuration.get("contract_id")),
            ("提交参数", configuration.get("submitted", report["parameters"])),
            ("规范化参数", configuration.get("normalized", report["parameters"])),
            ("实际生效参数", configuration.get("effective")),
            ("自动调整", configuration.get("adjustments")),
        ):
            story.append(para(label, heading))
            if isinstance(value, dict):
                for key, item in value.items():
                    pair(key, json.dumps(item, ensure_ascii=False))
            else:
                pair(label, value)
        section("产物清单")
        for name, size in run.artifacts.items():
            pair(name, f"{size} bytes")
        story.append(para("完整轮次数据、日志和原始产物见 CSV / JSON / ZIP 下载。"))
        output = io.BytesIO()
        document = SimpleDocTemplate(
            output,
            pagesize=(595.28, 841.89),
            leftMargin=42,
            rightMargin=42,
            topMargin=38,
            bottomMargin=42,
            title=f"模型训练报告 - {run.name}",
            author="CV Platform",
        )

        def footer(canvas, doc):
            canvas.setFont("CVChinese", 8)
            canvas.setFillColor(colors.HexColor("#64748b"))
            canvas.drawString(42, 24, "CV Platform | 模型训练报告")
            canvas.drawRightString(553, 24, str(doc.page))

        document.build(story, onFirstPage=footer, onLaterPages=footer)
        return output.getvalue()


def write_bundle(service, run, target: Path) -> None:
    report = snapshot(service, run)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in run.artifacts:
            archive.write(
                service.artifact(run.id, name),
                arcname=name,
                compress_type=zipfile.ZIP_STORED
                if Path(name).suffix in {".pt", ".png", ".jpg"}
                else zipfile.ZIP_DEFLATED,
            )
        archive.writestr("report/report.json", json_bytes(report))
        archive.writestr("report/report.pdf", pdf_report(service, run, report))
        archive.writestr("report/metrics.csv", csv_bytes(report["metrics"]))
        archive.writestr(
            "report/per-class.csv", csv_bytes((report["evaluation"] or {}).get("per_class", []))
        )
        archive.writestr("report/logs.txt", "\n".join(service.logs(run.id, 2000)))
        archive.writestr("report/figures.zip", figures_zip(service, run, report))
