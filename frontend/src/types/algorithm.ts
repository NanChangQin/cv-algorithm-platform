export type AlgorithmStatus =
  | 'uploaded'
  | 'validating'
  | 'building'
  | 'testing'
  | 'available'
  | 'disabled'
  | 'failed'

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue }
export type JsonSchema = Record<string, unknown>

type ParameterMetadata = {
  title?: string
  description?: string
  group?: string
  common?: boolean
  status?: 'configurable' | 'managed' | 'unavailable'
  reason?: string | null
}

export type NumberParameter = ParameterMetadata & {
  type: 'number'
  default: number
  minimum?: number
  maximum?: number
  step?: number
}

export type IntegerParameter = ParameterMetadata & {
  type: 'integer'
  default: number
  minimum?: number
  maximum?: number
  step?: number
}

export type BooleanParameter = ParameterMetadata & { type: 'boolean'; default: boolean }
export type StringParameter = ParameterMetadata & { type: 'string'; default: string; options?: string[] }
export type JsonParameter = ParameterMetadata & { type: 'json'; default: JsonValue; json_schema: JsonSchema }
export type ParameterSpec = NumberParameter | IntegerParameter | BooleanParameter | StringParameter | JsonParameter

export interface ParameterContract {
  id: string
  sha256: string
  framework: string
  framework_version: string
  default_key_count: number
  mode: string
  task: string
  parameters: Record<string, ParameterSpec>
}

/** 由训练结果发布的版本记录的来源 */
export interface ModelLineage {
  export_job_id?: string
  export_name?: string
  training_run_id: string
  training_name: string
  dataset_id: string
  dataset_name: string | null
  dataset?: { id: string; version: number; sha256: string; source_sha256: string | null; split_config: Record<string, unknown> } | null
  class_names: string[]
  base_model: string
  checkpoint: 'best' | 'last'
  epoch: number | null
  epochs_trained: number
  parameters: { epochs: number; batch: number; imgsz: number; lr0: number; [key: string]: unknown }
  metrics: { precision: number | null; recall: number | null; map50: number | null; map50_95: number | null }
}

export interface Algorithm {
  id: string
  key: string
  name: string
  version: string
  description: string
  task_type: 'object_detection' | 'classification' | 'segmentation' | 'ocr' | 'pose_estimation'
  device: 'cpu' | 'gpu' | 'auto'
  framework: string
  status: AlgorithmStatus
  image: string
  parameters: Record<string, ParameterSpec>
  created_at: string
  package_sha256: string | null
  image_digest: string | null
  created_by: string
  traffic_weight: number
  container_status: string
  last_called_at: string | null
  project_id: string
  /** 由训练发布的版本才有 */
  lineage?: ModelLineage | null
}

export interface BuildJob {
  id: string
  algorithm_version_id: string
  status: 'queued' | 'building' | 'testing' | 'completed' | 'failed'
  logs: string[]
  created_at: string
  updated_at: string
  image_digest: string | null
  error_message: string | null
}
