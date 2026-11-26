import type { TrainingModel, TrainingRunSummary } from '@/api/training'

export const trainingTaskTypes = [
  { key: 'object_detection', label: '目标检测', description: '识别目标类别与位置', available: true },
  { key: 'classification', label: '图像分类', description: '判断整张图片的类别', available: false },
  { key: 'segmentation', label: '实例分割', description: '标记每个目标的像素轮廓', available: false },
  { key: 'pose_estimation', label: '姿态估计', description: '定位关键点与姿态', available: false },
  { key: 'oriented_detection', label: '旋转框检测', description: '识别带方向的目标边界', available: false },
] as const

export function trainingTaskLabel(key: string) {
  return trainingTaskTypes.find(item => item.key === key)?.label ?? key
}
export function trainingModelTask(model: TrainingModel) {
  return ({ detect: 'object_detection', classify: 'classification', segment: 'segmentation', pose: 'pose_estimation', obb: 'oriented_detection' } as Record<string, string>)[model.task ?? 'detect'] ?? model.task
}
export function trainingAlgorithm(run: TrainingRunSummary) {
  const architecture = run.progress?.architecture ?? run.model
  if (architecture === 'fasterrcnn_resnet50_fpn.pt') return 'Faster R-CNN'
  return architecture.match(/^yolo\d+/i)?.[0]?.toUpperCase() ?? '自定义模型'
}
export const executionLabels = { train: '从头训练', finetune: '权重微调', resume: '断点续训', val: '独立验证', legacy: '训练' }
export function trainingExecution(run: TrainingRunSummary): keyof typeof executionLabels {
  if (run.progress?.operation === 'val') return 'val'
  if (run.progress?.source?.mode === 'resume') return 'resume'
  if (run.parameters.pretrained === false) return 'train'
  if (run.progress?.source || run.parameters.pretrained === true) return 'finetune'
  return 'legacy'
}
