export type TaskStatus =
  | 'queued'
  | 'preparing'
  | 'starting'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface Detection {
  label: string
  score: number
  bbox: [number, number, number, number]
}

export interface ObjectDetectionResult {
  schema_version: '1.0'
  request_id: string
  type: 'object_detection'
  algorithm: { id: string; version: string }
  input: { width: number; height: number }
  timing: { preprocess_ms: number; inference_ms: number; postprocess_ms: number }
  data: { detections: Detection[] }
}

export interface ClassificationResult {
  schema_version: '1.0'; request_id: string; type: 'classification'
  algorithm: { id: string; version: string }; input: { width: number; height: number }
  timing: Timing; data: { predictions: Array<{ label: string; score: number }> }
}

export interface SegmentationResult {
  schema_version: '1.0'; request_id: string; type: 'segmentation'
  algorithm: { id: string; version: string }; input: { width: number; height: number }
  timing: Timing; data: { segments: Array<{ label: string; score: number; mask_uri: string }> }
}

export interface OcrResult {
  schema_version: '1.0'; request_id: string; type: 'ocr'
  algorithm: { id: string; version: string }; input: { width: number; height: number }
  timing: Timing
  data: { texts: Array<{ text: string; score: number; polygon: number[][] }> }
}

export interface PoseResult {
  schema_version: '1.0'; request_id: string; type: 'pose_estimation'
  algorithm: { id: string; version: string }; input: { width: number; height: number }
  timing: Timing
  data: { instances: Array<{ score: number; keypoints: Keypoint[] }> }
}

export interface Timing { preprocess_ms: number; inference_ms: number; postprocess_ms: number }
export interface Keypoint { name: string; x: number; y: number; score: number }
export type AlgorithmResult = ObjectDetectionResult | ClassificationResult | SegmentationResult | OcrResult | PoseResult

export interface InferenceTask {
  id: string
  algorithm_version_id: string
  asset_id: string | null
  parameters: Record<string, unknown>
  status: TaskStatus
  created_at: string
  updated_at: string
  started_at: string | null
  completed_at: string | null
  cancelled_at: string | null
  cancelled_by: string | null
  container_id: string | null
  error_code: string | null
  error_message: string | null
  retry_of: string | null
  project_id: string
  node_id: string | null
  gpu_device_ids: string[]
  execution_node_id: string | null
  execution_gpu_device_ids: string[]
}

export interface CreateTaskPayload {
  algorithm_version_id: string
  asset_id: string
  parameters: Record<string, unknown>
  node_id?: string
  gpu_device_ids?: string[]
}

export interface TaskResultResponse {
  task_id: string
  result: AlgorithmResult
}
