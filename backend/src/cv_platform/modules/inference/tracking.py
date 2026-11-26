"""Class-aware greedy IoU association, scoped to one continuous stream segment.

This deliberately exposes its limitations; it is not identity recognition/ReID.
"""

import math


def iou(a, b):
    area = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    total = (
        max(0, a[2] - a[0]) * max(0, a[3] - a[1]) + max(0, b[2] - b[0]) * max(0, b[3] - b[1]) - area
    )
    return area / total if total > 0 else 0


class IoUTracker:
    def __init__(self, threshold=0.3, max_gap=2.0):
        self.threshold, self.max_gap = threshold, max_gap
        self.tracks = {}
        self.next_id = 1

    def update(self, detections, timestamp):
        self.tracks = {
            k: v for k, v in self.tracks.items() if 0 <= timestamp - v["time"] <= self.max_gap
        }
        candidates = []
        for i, item in enumerate(detections):
            if not all(math.isfinite(x) for x in item["bbox"]):
                continue
            for track_id, previous in self.tracks.items():
                score = (
                    iou(item["bbox"], previous["bbox"]) if item["label"] == previous["label"] else 0
                )
                if score >= self.threshold:
                    candidates.append((score, i, track_id))
        assigned, used = {}, set()
        for _, i, track_id in sorted(candidates, reverse=True):
            if i not in assigned and track_id not in used:
                assigned[i] = track_id
                used.add(track_id)
        output = []
        for i, item in enumerate(detections):
            track_id = assigned.get(i)
            if track_id is None:
                track_id = self.next_id
                self.next_id += 1
            a, b, c, d = item["bbox"]
            trail = self.tracks.get(track_id, {}).get("trail", [])[-29:] + [
                [(a + c) / 2, (b + d) / 2]
            ]
            self.tracks[track_id] = {**item, "time": timestamp, "trail": trail}
            output.append({**item, "track_id": track_id, "trail": trail})
        return output
