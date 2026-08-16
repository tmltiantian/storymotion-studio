export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };

export type ProjectMode = "original" | "novel" | "replica";
export type ApprovalPreset = "quick" | "standard" | "strict";
export type StageName =
  | "concept"
  | "script"
  | "storyboard"
  | "assets"
  | "audio"
  | "video"
  | "edit"
  | "eval"
  | "deliver";
export type ExecutionState =
  | "pending"
  | "ready"
  | "running"
  | "passed"
  | "failed"
  | "blocked"
  | "stale";
export type ReviewPolicy =
  | "manual"
  | "automatic"
  | "grouped"
  | "not_applicable";
export type ReviewState =
  | "not_ready"
  | "awaiting_review"
  | "approved"
  | "changes_requested"
  | "auto_approved"
  | "skipped";
export type RequiredAction =
  | "approve_review_evidence"
  | "address_review_changes"
  | "fix_stage_error_and_resume"
  | "run_or_resume"
  | "none";

export interface Artifact {
  artifact_id: string;
  name: string;
  media_type: string;
  media_url: string;
  kind?: ArtifactKind;
  viewer?: ArtifactViewerMetadata;
}

export type ArtifactKind = "text" | "image" | "audio" | "video" | "eval" | "file";

export interface DialogueTiming {
  dialogue_id: string;
  speaker: string;
  start_seconds: number;
  end_seconds: number;
  text?: string;
}

export interface ArtifactViewerMetadata {
  size_bytes?: number;
  width?: number;
  height?: number;
  fps?: number;
  shot_id?: string;
  dialogues?: DialogueTiming[];
}

export interface StageDetail {
  stage: StageName;
  execution_state: ExecutionState;
  review_state: ReviewState;
  review_policy: ReviewPolicy;
  review_blocks_progress: boolean;
  revision: number;
  executor: string;
  blocked_reasons: string[];
  error: string;
  artifacts: Artifact[];
}

export interface ProjectDetail {
  project_id: string;
  title: string;
  mode: ProjectMode;
  target: JsonObject;
  next_stage: StageName | "complete";
  required_action: RequiredAction;
  stages: StageDetail[];
  final_outputs: Artifact[];
  eval_reports: Artifact[];
}

export interface CreateProjectRequest {
  project_id: string;
  title: string;
  mode: ProjectMode;
  idea?: string;
  source_artifact_id?: string;
  target?: JsonObject;
  approval_preset?: ApprovalPreset;
}

export interface JobAccepted {
  job_id: string;
  status: "queued";
}

export interface RunStageRequest {
  enable_live?: boolean;
}

export interface ApproveStageRequest {
  revision: number;
  note: string;
  evidence_artifact_ids: string[];
}

export interface RequestChangesRequest {
  revision: number;
  reason: string;
}

export type ChangeScope = "dialogue" | "character" | "shot" | "subtitle_style";

export interface ImpactRequest {
  stage: StageName;
  scope: ChangeScope;
  dialogue_ids?: string[];
  character_ids?: string[];
  shot_ids?: string[];
  subtitle_style?: boolean;
}

export interface ImpactEntry {
  stage: StageName;
  item_count: number;
}

export interface PublicImpactRequest {
  stage: StageName;
  scope: ChangeScope;
  subtitle_style: boolean;
  selection_counts: {
    dialogue: number;
    character: number;
    shot: number;
  };
}

export interface ImpactSummary {
  schema_version: "motion-comic-factory.impact-summary.v2";
  regenerated_video_shot_count: number;
  reused_video_shot_count: number;
  regenerated_audio_item_count: number;
  affected_stages: StageName[];
  estimate: { available: false };
}

export interface ImpactPlan {
  schema_version: "motion-comic-factory.impact-plan.v2";
  plan_id: string;
  request: PublicImpactRequest;
  entries: ImpactEntry[];
  summary: ImpactSummary;
  preserved_artifacts: string[];
  package_sha256: string;
  episode_sha256: string;
}

export interface VideoShotRequest {
  shot_id: string;
  duration: number;
  resolution: string;
}

export interface VideoGenerationRequest {
  schema_version: "motion-comic-factory.video-generation-request.v1";
  project_id: string;
  project_sha256: string;
  package_sha256: string;
  revision_hashes: Record<string, string>;
  artifact_hashes: Record<string, string>;
  approval_hashes: Record<string, string>;
  repair_plan_sha256: string;
  shot_ids: string[];
  shots: VideoShotRequest[];
  provider: string;
  model: string;
  resolution: string;
  output_seconds: number;
  estimated_cost_yuan: number;
  price_yuan_per_second: number;
}

export interface VideoPreflight extends VideoGenerationRequest {
  ready: boolean;
  blockers: string[];
}

export interface ConfirmedVideoPreflight {
  generation_token: string;
  generation_request: VideoGenerationRequest;
}

export interface VideoGenerationSubmission {
  generation_token: string;
  generation_request: VideoGenerationRequest;
}

export type JobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface JobDetail {
  job_id: string;
  project_id: string;
  operation: string;
  status: JobStatus;
  created_at: string;
  updated_at: string;
  provider_tasks: JsonObject;
  result: JsonObject;
  error: string;
  resume_count: number;
  last_event_sequence?: number;
}

export type ResumeJobResponse = JobAccepted | JobDetail;

export interface JobEvent {
  job_id: string;
  sequence: number;
  kind: string;
  data: JsonObject;
  created_at: string;
}

export interface VideoWorkspaceShot {
  shot_id: string;
  duration_seconds: number;
}

export interface VideoWorkspace {
  schema_version: "motion-comic-factory.video-workspace.v1";
  project_id: string;
  shots: VideoWorkspaceShot[];
  job: JobDetail | null;
}

export interface ProviderCapability {
  provider: string;
  model: string;
  ready: boolean;
  blockers: string[];
  enabled: boolean;
  supports_reference_images?: boolean;
  voice_configured?: boolean;
}

export interface ProviderSettings {
  capabilities: Partial<
    Record<"text" | "image" | "video" | "audio", ProviderCapability>
  >;
}

export interface WorkSummary {
  work_id: string;
  project_id: string;
  title: string;
  mode: ProjectMode;
  delivered_at: string;
  cover?: Artifact;
  current_version?: string;
}

export interface WorkDetail extends WorkSummary {
  outputs: Artifact[];
  versions: JsonObject[];
  eval_reports: Artifact[];
}

export interface WorkCatalogAdapter {
  listWorks(): Promise<WorkSummary[]>;
  getWork(workId: string): Promise<WorkDetail>;
}

export type WorkCapability =
  | {
      availability: "unavailable";
      reason: "local_catalog_not_configured";
    }
  | {
      availability: "available";
      catalog: WorkCatalogAdapter;
    };
