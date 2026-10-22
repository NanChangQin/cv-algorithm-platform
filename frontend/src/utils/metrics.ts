import type { EvaluationTaskType } from '@/api/evaluations'

export const metricLabel: Record<string, string> = {
  precision_iou50: '精确率', recall_iou50: '召回率', f1_iou50: 'F1',
  true_positive: '正确检出', false_positive: '误检', false_negative: '漏检',
  top1_accuracy: 'Top-1 准确率', top5_accuracy: 'Top-5 准确率',
  label_precision: '类别精确率', label_recall: '类别召回率', label_f1: '类别 F1',
  character_accuracy: '字符准确率', edit_distance: '平均编辑距离',
  pck: 'PCK', keypoints_compared: '平均比对关键点',
}

export const metricColumns: Record<EvaluationTaskType, string[]> = {
  object_detection: ['precision_iou50', 'recall_iou50', 'f1_iou50', 'true_positive', 'false_positive', 'false_negative'],
  classification: ['top1_accuracy', 'top5_accuracy'],
  segmentation: ['label_precision', 'label_recall', 'label_f1'],
  ocr: ['character_accuracy', 'edit_distance'],
  pose_estimation: ['pck', 'keypoints_compared'],
}

const countMetrics = new Set(['true_positive', 'false_positive', 'false_negative', 'keypoints_compared', 'edit_distance'])
/** 这些指标越小越好 */
export const lowerIsBetter = new Set(['false_positive', 'false_negative', 'edit_distance'])
/** 数量类指标只比较优劣，不需要“最好”高亮的列 */
const notRanked = new Set(['true_positive', 'keypoints_compared'])

export function formatMetric(key: string, value: number | undefined) {
  if (value === undefined || !Number.isFinite(value)) return '—'
  if (countMetrics.has(key)) return Number.isInteger(value) ? String(value) : value.toFixed(1)
  return `${(value * 100).toFixed(1)}%`
}

/** 每列的最佳值；没有可比较的数据时返回 undefined */
export function bestValue(key: string, values: Array<number | undefined>) {
  if (notRanked.has(key)) return undefined
  const present = values.filter((value): value is number => typeof value === 'number')
  if (present.length < 2) return undefined
  return lowerIsBetter.has(key) ? Math.min(...present) : Math.max(...present)
}

export const annotationExamples: Record<EvaluationTaskType, { accept: string; text: string }> = {
  object_detection: {
    accept: '.json',
    text: 'COCO 格式（images / annotations / categories，bbox 为 [x, y, 宽, 高]），或：\n{"items": [{"file": "a.jpg", "detections": [{"label": "person", "bbox": [x1, y1, x2, y2]}]}]}',
  },
  classification: { accept: '.csv,.json', text: 'CSV：文件名,标签（可以有表头），或：\n{"items": [{"file": "a.jpg", "label": "cat"}]}' },
  segmentation: { accept: '.json', text: '{"items": [{"file": "a.jpg", "labels": ["road", "car"]}]}' },
  ocr: { accept: '.csv,.json', text: 'CSV：文件名,文本，或：\n{"items": [{"file": "a.jpg", "text": "京A12345"}]}' },
  pose_estimation: {
    accept: '.json',
    text: '{"items": [{"file": "a.jpg", "keypoints": [{"name": "nose", "x": 120, "y": 80}], "threshold": 0.05}]}',
  },
}
