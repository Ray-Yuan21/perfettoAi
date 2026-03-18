export interface Trace {
  id: string;
  filename: string;
  score: number | null;
}

export interface AnalyzerOption {
  id: string;
  label: string;
  description: string;
}

export interface CallTreeNode {
  name: string;
  dur_ms: number;
  self_ms: number;
  thread: string;
  ts?: number;
  dur?: number;
  children: CallTreeNode[];
}

export interface CriticalPathNode {
  name: string;
  dur_ms: number;
  self_ms: number;
  thread: string;
  ts?: number;
  dur?: number;
}

export interface EvidenceSQL {
  label: string;
  sql: string;
  conclusion: string;
}

export interface FrameAnalysis {
  flow_description: string;
  bottleneck_function: string;
  bottleneck_reason: string;
  root_cause_category: string;
  severity: string;
  side: string;
  evidence_sql?: EvidenceSQL[];
}

export interface JankFrame {
  ts: number;
  dur: number;
  dur_ms: number;
  overrun_ms: number;
  jank_type: string;
  present_type: string;
  layer_name: string;
  process_name: string;
  pid: number;
  upid: number;
  critical_path?: CriticalPathNode[];
  root_cause?: string;
  call_tree?: CallTreeNode[];
  analysis?: FrameAnalysis;
  cpu_scheduling?: Record<string, number>;
}

export interface Issue {
  severity: "critical" | "high" | "medium" | "low";
  description: string;
  category: string;
}

export interface RootCause {
  frame_id: number;
  cause: string;
}

export interface JankCauseCluster {
  cause: string;
  frames?: number[];
  count: number;
  description?: string;
  severity?: string;
  suggestion?: string;
}

export interface JankInsights {
  bottleneck_type?: string;
  user_impact_assessment?: string;
  hardware_assessment?: string;
  root_cause?: RootCause[];
  jank_cause_clusters?: JankCauseCluster[];
  summary?: string;
  app_jank_summary?: string;
  sf_jank_summary?: string;
}

export interface CategoryReport {
  analyzer_name: string;
  status: string;
  statistics: Record<string, unknown>;
  llm_insights: Record<string, unknown> | null;
  issues: Issue[];
  suggestions: string[];
  score: number | null;
}

export interface OverallScore {
  score: number;
  overall: number;
  category_scores: Record<string, number>;
}

export interface TraceResult {
  trace_path: string;
  metadata: Record<string, unknown>;
  category_reports: CategoryReport[];
  overall_score: OverallScore | null;
  ranked_issues: Issue[];
  jank_frames: JankFrame[];
}

export interface UploadResponse {
  trace_id: string;
  filename: string;
}

export interface AnalysisStatus {
  status: "analyzing" | "done" | "failed";
  progress?: string;
}

export interface LLMSettings {
  provider: string;
  api_endpoint: string;
  model_name: string;
  api_key_set: boolean;
  temperature: number;
  max_tokens: number;
}
