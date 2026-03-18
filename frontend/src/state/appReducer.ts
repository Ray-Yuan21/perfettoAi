import type { JankFrame, TraceResult } from "../api/types";

export type AnalysisState = "idle" | "uploading" | "analyzing" | "done" | "failed";
export type TabId = string;

export interface AppState {
  // Core Data
  data: TraceResult | null;
  perfettoUrl: string | null;
  analysisState: AnalysisState;
  status: string;

  // File Upload
  fileName: string | null;
  pendingFile: File | null;
  selectedAnalyzers: TabId[];
  dragOver: boolean;

  // UI State
  activeTab: TabId;
  leftWidth: number;
  settingsOpen: boolean;
  selectedFrame: JankFrame | null;
}

export const initialState: AppState = {
  data: null,
  perfettoUrl: null,
  analysisState: "idle",
  status: "",

  fileName: null,
  pendingFile: null,
  selectedAnalyzers: ["jank"],
  dragOver: false,

  activeTab: "jank",
  leftWidth: 340,
  settingsOpen: false,
  selectedFrame: null,
};

export type AppAction =
  | { type: "RESET_ANALYSIS" }
  | { type: "SET_PENDING_FILE"; payload: File | null }
  | { type: "START_UPLOAD"; payload: { fileName: string } }
  | { type: "UPLOAD_SUCCESS"; payload: { perfettoUrl: string } }
  | { type: "SET_STATUS"; payload: { status: string } }
  | { type: "ANALYSIS_COMPLETE"; payload: { data: TraceResult } }
  | { type: "ANALYSIS_FAILED"; payload: { error: string } }
  | { type: "SET_ACTIVE_TAB"; payload: TabId }
  | { type: "SET_LEFT_WIDTH"; payload: number }
  | { type: "SET_SETTINGS_OPEN"; payload: boolean }
  | { type: "SET_SELECTED_FRAME"; payload: JankFrame | null }
  | { type: "TOGGLE_ANALYZER"; payload: TabId }
  | { type: "SET_DRAG_OVER"; payload: boolean };

export function appReducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case "RESET_ANALYSIS":
      return {
        ...state,
        data: null,
        perfettoUrl: null,
        analysisState: "idle",
        status: "",
        fileName: null,
        pendingFile: null,
        selectedFrame: null,
      };

    case "SET_PENDING_FILE":
      return {
        ...state,
        pendingFile: action.payload,
        dragOver: false,
      };

    case "START_UPLOAD":
      return {
        ...state,
        analysisState: "uploading",
        status: "Uploading...",
        data: null,
        fileName: action.payload.fileName,
        pendingFile: null,
      };

    case "UPLOAD_SUCCESS":
      return {
        ...state,
        perfettoUrl: action.payload.perfettoUrl,
        analysisState: "analyzing",
        status: "Analyzing...",
      };

    case "SET_STATUS":
      return {
        ...state,
        status: action.payload.status,
      };

    case "ANALYSIS_COMPLETE":
      return {
        ...state,
        data: action.payload.data,
        analysisState: "done",
        status: "Analysis complete",
        selectedFrame: null,
      };

    case "ANALYSIS_FAILED":
      return {
        ...state,
        analysisState: "failed",
        status: action.payload.error,
      };

    case "SET_ACTIVE_TAB":
      return { ...state, activeTab: action.payload };

    case "SET_LEFT_WIDTH":
      return { ...state, leftWidth: action.payload };

    case "SET_SETTINGS_OPEN":
      return { ...state, settingsOpen: action.payload };

    case "SET_SELECTED_FRAME":
      return { ...state, selectedFrame: action.payload };

    case "TOGGLE_ANALYZER":
      return {
        ...state,
        selectedAnalyzers: state.selectedAnalyzers.includes(action.payload)
          ? state.selectedAnalyzers.filter((a) => a !== action.payload)
          : [...state.selectedAnalyzers, action.payload],
      };

    case "SET_DRAG_OVER":
      return { ...state, dragOver: action.payload };

    default:
      return state;
  }
}
