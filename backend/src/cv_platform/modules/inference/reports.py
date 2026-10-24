import csv
import io
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image as PDFImage
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from .service import now

STATUS = {
    "queued": "排队中",
    "running": "运行中",
    "reconnecting": "重连中",
    "stopping": "正在停止",
    "finalizing": "生成产物",
    "completed": "已完成",
    "failed": "失败",
    "stopped": "已停止",
    "interrupted": "已中断",
}


def frames(service, state, start=None, end=None):
    count = state["processed"]
    for offset in range(0, count, 100):
        for frame in service.store.frames(state["id"], min(100, count - offset), offset):
            timestamp = (frame["timestamp_ms"] or 0) / 1000
            if (start is None or timestamp >= start) and (end is None or timestamp < end):
                yield frame


def selected_detections(frame, label=None, confidence=0):
    result = frame.get("result")
    if not result or result["type"] != "object_detection":
        return []
    return [
        d
        for d in frame["tracks"] or result["data"]["detections"]
        if d["score"] >= confidence and (not label or d["label"] == label)
    ]


def snapshot(service, state, start=None, end=None, label=None, confidence=0):
    counts = Counter()
    trajectories = set()
    timing = []
    timeline = []
    samples = []
    execution = {}
    effective = {}
    processed = failed = 0
    for frame in frames(service, state, start, end):
        processed += 1
        failed += frame["status"] != "completed"
        detections = selected_detections(frame, label, confidence)
        for d in detections:
            counts[d["label"]] += 1
            if "track_id" in d:
                trajectories.add((frame["segment"], d["track_id"]))
        result = frame.get("result")
        actual = frame.get("execution")
        if actual:
            execution[json.dumps(actual, sort_keys=True)] = actual
        if result and result.get("configuration"):
            effective[json.dumps(result["configuration"], sort_keys=True)] = result["configuration"]
        ms = (result.get("timing") or {}).get("inference_ms") if result else None
        if ms is not None:
            timing.append(ms)
        timeline.append(
            {
                "index": frame["index"],
                "timestamp_ms": frame["timestamp_ms"],
                "count": len(detections),
                "inference_ms": ms,
            }
        )
        if len(samples) < 8 and result and not label and not confidence:
            samples.append(
                {
                    "index": frame["index"],
                    "timestamp_ms": frame["timestamp_ms"],
                    "input": frame["input_name"],
                }
            )
    elapsed = None
    if state.get("started_at"):
        elapsed = (
            datetime.fromisoformat(state.get("finished_at") or state["updated_at"])
            - datetime.fromisoformat(state["started_at"])
        ).total_seconds()
    return {
        "schema_version": 1,
        "snapshot_at": state["updated_at"],
        "generated_at": now(),
        "session": state,
        "scope": {
            "start_seconds_inclusive": start,
            "end_seconds_exclusive": end,
            "label": label,
            "display_confidence": confidence,
            "mode": "filtered"
            if start is not None or end is not None or label or confidence
            else "all",
        },
        "summary": {
            "processed": processed,
            "failed": failed,
            "detections": sum(counts.values()),
            "class_counts": dict(counts),
            "tracks": len(trajectories) if state["config"]["tracking"] else None,
            "mean_inference_ms": sum(timing) / len(timing) if timing else None,
            "session_elapsed_seconds": elapsed,
            "session_processing_fps": state["processed"] / elapsed
            if elapsed and elapsed > 0
            else None,
        },
        "execution": list(execution.values()),
        "effective_configurations": list(effective.values()),
        "timeline": timeline,
        "samples": samples,
        "notes": [
            *state["notes"],
            "无真实标注，不计算准确率、召回率或 mAP。",
            "类别或置信度筛选报告不附带未筛选的样例图片和视频。",
            "累计检测框数、轨迹数均不代表独立身份人数。",
            "时间筛选使用源时间/会话相对秒，区间为左闭右开；性能帧率按整个会话计算。",
            "标注视频与采样画面保留源时间间隔，不含音轨；录像为已采集画面，不是原始全帧录像。",
            "跟踪器：按类别的 IoU 关联（阈值 0.3，最长间隔 2 秒），不含重识别。"
            if state["config"]["tracking"]
            else "未启用目标跟踪。",
        ],
    }


def csv_bytes(service, report, destination=None):
    output = destination if destination is not None else io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "session_id",
            "frame_index",
            "source_frame_index",
            "timestamp_ms",
            "segment",
            "task_id",
            "input",
            "input_sha256",
            "status",
            "label",
            "confidence",
            "x1_px",
            "y1_px",
            "x2_px",
            "y2_px",
            "track_id",
            "inference_ms",
            "result_json",
        ]
    )
    scope = report["scope"]
    for f in frames(
        service, report["session"], scope["start_seconds_inclusive"], scope["end_seconds_exclusive"]
    ):
        result = f.get("result")
        ms = (result.get("timing") or {}).get("inference_ms") if result else None
        common = [
            report["session"]["id"],
            f["index"],
            f["source_index"],
            f["timestamp_ms"],
            f["segment"],
            f["task_id"],
            f["input_name"],
            f["input_sha256"],
            f["status"],
        ]
        ds = selected_detections(f, scope["label"], scope["display_confidence"])
        for d in ds:
            writer.writerow(
                common + [d["label"], d["score"], *d["bbox"], d.get("track_id"), ms, ""]
            )
        if not ds:
            writer.writerow(
                common
                + [
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    ms,
                    json.dumps(result["data"], ensure_ascii=False)
                    if result and result["type"] != "object_detection"
                    else "",
                ]
            )
    if destination is None:
        return ("\ufeff" + output.getvalue()).encode("utf-8")
    return None


def chinese_font():
    candidates = [
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        Path("../runtimes/yolo-trainer/assets/Arial.Unicode.ttf"),
    ]
    return next(p for p in candidates if p.exists())


def chart_png(report):
    from matplotlib import font_manager
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    font = font_manager.FontProperties(fname=str(chinese_font()))
    fig = Figure(figsize=(10, 6.5), layout="constrained")
    ax, timeline_ax = fig.subplots(2, 1)
    values = report["summary"]["class_counts"]
    if values:
        ax.bar(range(len(values)), list(values.values()), color="#2563eb")
        ax.set_xticks(range(len(values)), list(values), fontproperties=font)
    else:
        ax.text(0.5, 0.5, "No detection data in this scope", ha="center", transform=ax.transAxes)
    ax.set_title("检测框类别分布（非去重人数）", fontproperties=font)
    points = report["timeline"]
    if points:
        positions = [
            (p["timestamp_ms"] / 1000 if p["timestamp_ms"] is not None else p["index"])
            for p in points
        ]
        timeline_ax.plot(positions, [p["count"] for p in points], color="#2563eb", marker=".")
        timeline_ax.set_ylabel("本帧检测框数", fontproperties=font)
        latency_ax = timeline_ax.twinx()
        latency_ax.plot(positions, [p["inference_ms"] for p in points], color="#f59e0b", alpha=0.8)
        latency_ax.set_ylabel("模型推理耗时（ms）", fontproperties=font)
    timeline_ax.set_xlabel("源时间（秒）/ 图片序号", fontproperties=font)
    timeline_ax.set_title("处理过程（抽帧间隙不代表已处理画面）", fontproperties=font)
    output = io.BytesIO()
    FigureCanvasAgg(fig).print_png(output)
    return output.getvalue()


def pdf_bytes(report, root):
    if "InferenceChinese" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("InferenceChinese", str(chinese_font())))
    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName = "InferenceChinese"
        style.wordWrap = "CJK"
    styles["BodyText"].leading = 16
    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output, pagesize=(595, 842), leftMargin=38, rightMargin=38, topMargin=38, bottomMargin=38
    )
    story = []

    def paragraph(text, style="BodyText"):
        story.append(Paragraph(escape(str(text)), styles[style]))
        story.append(Spacer(1, 7))

    state = report["session"]
    summary = report["summary"]
    paragraph("模型推理报告", "Title")
    for text in [
        state["name"],
        f"会话：{state['id']}",
        f"状态：{STATUS.get(state['status'], state['status'])}",
        f"统计截止：{datetime.fromisoformat(report['snapshot_at']).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S UTC+08:00')}",
        f"模型：{state['model']['name']} · v{state['model']['version']}",
        f"来源：{(state.get('source') or {}).get('name') or {'images': '图片批次', 'browser': '浏览器摄像头'}.get(state['kind'], state['kind'])}",
        f"导出范围：{'全部结果' if report['scope']['mode'] == 'all' else '筛选结果'}；类别：{report['scope']['label'] or '全部'}；展示置信度下限：{report['scope']['display_confidence']}",
        f"时间范围（左闭右开）：{report['scope']['start_seconds_inclusive'] if report['scope']['start_seconds_inclusive'] is not None else '起点'} 至 {report['scope']['end_seconds_exclusive'] if report['scope']['end_seconds_exclusive'] is not None else '统计截止'} 秒",
    ]:
        paragraph(text)
    paragraph("处理统计", "Heading2")
    for key, title in [
        ("processed", "已处理图片/帧"),
        ("failed", "失败图片/帧"),
        ("detections", "累计检测框"),
        ("tracks", "累计轨迹"),
        ("mean_inference_ms", "平均模型推理耗时（ms）"),
        ("session_processing_fps", "会话整体处理帧率"),
    ]:
        value = summary[key]
        text = (
            f"{value:.2f}"
            if isinstance(value, float)
            else str(value)
            if value is not None
            else "未采集 / 不适用"
        )
        paragraph(f"{title}：{text}")
    story.append(PDFImage(io.BytesIO(chart_png(report)), width=510, height=331.5))
    paragraph("配置与可追溯信息", "Heading2")
    for key, value in state["config"].items():
        paragraph(f"{key}：{json.dumps(value, ensure_ascii=False)}")
    paragraph("实际执行设备：" + json.dumps(report["execution"], ensure_ascii=False))
    paragraph(
        "模型返回的生效配置："
        + (
            json.dumps(report["effective_configurations"], ensure_ascii=False)
            if report["effective_configurations"]
            else "模型未返回额外配置，已记录提交参数"
        )
    )
    paragraph("模型来源：" + json.dumps(state["model"], ensure_ascii=False))
    paragraph("输入摘要：" + json.dumps(state["inputs"][:50], ensure_ascii=False))
    paragraph("代表性结果", "Heading2")
    for sample in report["samples"]:
        path = root / f"result-{sample['index']:08d}.jpg"
        if path.exists():
            paragraph(
                f"样例 #{sample['index']} · {sample['input']} · 源时间 {sample['timestamp_ms']} ms"
            )
            with Image.open(path) as im:
                width, height = im.size
            ratio = min(510 / width, 260 / height)
            story.append(PDFImage(str(path), width=width * ratio, height=height * ratio))
    paragraph("完整性与限制", "Heading2")
    for note in report["notes"]:
        paragraph(note)
    paragraph("可用视频产物：" + json.dumps(state["artifacts"], ensure_ascii=False))

    def footer(canvas, document):
        canvas.setFont("InferenceChinese", 9)
        canvas.drawRightString(557, 20, f"第 {document.page} 页")

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()


def download(service, report, name):
    root = service.root / report["session"]["id"]
    if name == "summary.csv":
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["metric", "value", "snapshot_at", "scope"])
        for key, value in report["summary"].items():
            writer.writerow(
                [
                    key,
                    json.dumps(value, ensure_ascii=False),
                    report["snapshot_at"],
                    json.dumps(report["scope"], ensure_ascii=False),
                ]
            )
        return ("\ufeff" + output.getvalue()).encode("utf-8"), "text/csv; charset=utf-8"
    if name == "metrics.csv":
        return csv_bytes(service, report), "text/csv; charset=utf-8"
    if name == "report.json":
        return json.dumps(report, ensure_ascii=False, indent=2).encode(), "application/json"
    if name == "report.pdf":
        return pdf_bytes(report, root), "application/pdf"
    if name == "classes.png":
        return chart_png(report), "image/png"
    if name == "results.json":
        s = report["scope"]
        data = list(
            frames(
                service, report["session"], s["start_seconds_inclusive"], s["end_seconds_exclusive"]
            )
        )
        for item in data:
            if item.get("result") and item["result"]["type"] == "object_detection":
                item["result"]["data"]["detections"] = selected_detections(
                    item, s["label"], s["display_confidence"]
                )
                item["tracks"] = [
                    d for d in item["result"]["data"]["detections"] if "track_id" in d
                ]
        return json.dumps(
            {"scope": s, "frames": data}, ensure_ascii=False
        ).encode(), "application/json"
    if name == "bundle.zip":
        output = io.BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            for file in [
                "report.json",
                "report.pdf",
                "metrics.csv",
                "summary.csv",
                "results.json",
                "classes.png",
            ]:
                archive.writestr(file, download(service, report, file)[0])
            # Videos represent the whole session, so only include in all-results bundles.
            if report["scope"]["mode"] == "all":
                for file in report["session"]["artifacts"]:
                    if (root / file).is_file():
                        archive.write(root / file, file)
            for sample in report["samples"]:
                file = f"result-{sample['index']:08d}.jpg"
                if (root / file).exists():
                    archive.write(root / file, "samples/" + file)
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "snapshot_at": report["snapshot_at"],
                        "scope": report["scope"],
                        "files": archive.namelist(),
                        "note": "样例图为代表性帧；完整逐帧数据见 results.json。",
                    },
                    ensure_ascii=False,
                ),
            )
        return output.getvalue(), "application/zip"
    raise ValueError("unknown download")


def export_file(service, report, name, target):
    """Write large details and archives to disk, keeping memory bounded to a page of frames."""
    import tempfile

    if name == "metrics.csv":
        with target.open("w", encoding="utf-8-sig", newline="") as stream:
            csv_bytes(service, report, stream)
        return "text/csv; charset=utf-8"
    if name == "results.json":
        scope = report["scope"]
        with target.open("w", encoding="utf-8") as stream:
            stream.write('{"scope":' + json.dumps(scope, ensure_ascii=False) + ',"frames":[')
            first = True
            for item in frames(
                service,
                report["session"],
                scope["start_seconds_inclusive"],
                scope["end_seconds_exclusive"],
            ):
                if item.get("result") and item["result"]["type"] == "object_detection":
                    item["result"]["data"]["detections"] = selected_detections(
                        item, scope["label"], scope["display_confidence"]
                    )
                    item["tracks"] = [
                        d for d in item["result"]["data"]["detections"] if "track_id" in d
                    ]
                if not first:
                    stream.write(",")
                json.dump(item, stream, ensure_ascii=False)
                first = False
            stream.write("]}")
        return "application/json"
    if name == "bundle.zip":
        root = service.root / report["session"]["id"]
        with (
            tempfile.TemporaryDirectory(prefix="cv-report-") as directory,
            ZipFile(target, "w", ZIP_DEFLATED) as archive,
        ):
            for child in [
                "report.json",
                "report.pdf",
                "metrics.csv",
                "summary.csv",
                "results.json",
                "classes.png",
            ]:
                path = Path(directory) / child
                export_file(service, report, child, path)
                archive.write(path, child)
            if report["scope"]["mode"] == "all":
                for child in report["session"]["artifacts"]:
                    if (root / child).is_file():
                        archive.write(root / child, child)
            for sample in report["samples"]:
                child = f"result-{sample['index']:08d}.jpg"
                if (root / child).exists():
                    archive.write(root / child, "samples/" + child)
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "snapshot_at": report["snapshot_at"],
                        "scope": report["scope"],
                        "files": archive.namelist(),
                        "note": "样例图为代表性帧；完整逐帧数据见 results.json。",
                    },
                    ensure_ascii=False,
                ),
            )
        return "application/zip"
    content, media_type = download(service, report, name)
    target.write_bytes(content)
    return media_type
