from collections import Counter
from threading import RLock


class PlatformMetrics:
    """Small dependency-free Prometheus text exporter."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._requests: Counter[tuple[str, str]] = Counter()
        self._latency_count: Counter[str] = Counter()
        self._latency_sum: Counter[str] = Counter()

    def observe_http(self, method: str, status: int, elapsed_seconds: float) -> None:
        with self._lock:
            self._requests[(method, str(status))] += 1
            self._latency_count[method] += 1
            self._latency_sum[method] += elapsed_seconds

    def render(
        self,
        snapshot: dict[str, object],
        queue_depths: dict[str, int] | None = None,
    ) -> str:
        lines = [
            "# HELP cv_platform_http_requests_total HTTP requests handled by the platform API.",
            "# TYPE cv_platform_http_requests_total counter",
        ]
        with self._lock:
            for (method, status), value in sorted(self._requests.items()):
                lines.append(
                    f'cv_platform_http_requests_total{{method="{method}",status="{status}"}} '
                    f"{value}"
                )
            lines.extend(
                [
                    "# HELP cv_platform_http_request_duration_seconds_sum Total HTTP request time.",
                    "# TYPE cv_platform_http_request_duration_seconds_sum counter",
                ]
            )
            for method, value in sorted(self._latency_sum.items()):
                lines.append(
                    f'cv_platform_http_request_duration_seconds_sum{{method="{method}"}} '
                    f"{value:.9f}"
                )
            lines.append(
                "# HELP cv_platform_http_request_duration_seconds_count Observed HTTP requests."
            )
            lines.append("# TYPE cv_platform_http_request_duration_seconds_count counter")
            for method, value in sorted(self._latency_count.items()):
                lines.append(
                    f'cv_platform_http_request_duration_seconds_count{{method="{method}"}} {value}'
                )

        lines.extend(
            [
                "# HELP cv_platform_tasks Persisted inference tasks by status.",
                "# TYPE cv_platform_tasks gauge",
            ]
        )
        counts = snapshot["tasks_by_status"]
        for status in (
            "queued", "preparing", "starting", "running", "completed", "failed", "cancelled",
        ):
            lines.append(f'cv_platform_tasks{{status="{status}"}} {counts.get(status, 0)}')
        lines.extend(
            [
                "# HELP cv_platform_average_inference_milliseconds Average model inference time.",
                "# TYPE cv_platform_average_inference_milliseconds gauge",
                f'cv_platform_average_inference_milliseconds {snapshot["average_inference_ms"]}',
                "# HELP cv_platform_runtime_instances Known algorithm runtime instances.",
                "# TYPE cv_platform_runtime_instances gauge",
                f'cv_platform_runtime_instances {snapshot["runtime_instances"]}',
            ]
        )
        if queue_depths is not None:
            lines.extend(
                [
                    "# HELP cv_platform_queue_depth Durable queue depth.",
                    "# TYPE cv_platform_queue_depth gauge",
                    'cv_platform_queue_depth{kind="inference",state="queued"} '
                    f'{queue_depths["inference_queued"]}',
                    'cv_platform_queue_depth{kind="inference",state="active"} '
                    f'{queue_depths["inference_active"]}',
                    'cv_platform_queue_depth{kind="build",state="queued"} '
                    f'{queue_depths["builds_queued"]}',
                    'cv_platform_queue_depth{kind="build",state="active"} '
                    f'{queue_depths["builds_active"]}',
                ]
            )
        return "\n".join(lines) + "\n"


METRICS = PlatformMetrics()
