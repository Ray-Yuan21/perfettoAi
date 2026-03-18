import { useCallback, useEffect, useRef } from "react";

import {
  fetchAnalysisStatus,
  fetchTraceResult,
  getPerfettoUrl,
  uploadTrace,
} from "../api/client";
import type { AppAction, TabId } from "../state/appReducer";

interface UseTraceAnalysisResult {
  handleFile: (file: File, analyzers: TabId[]) => Promise<void>;
  resetAnalysis: () => void;
}

export default function useTraceAnalysis(
  dispatch: React.Dispatch<AppAction>
): UseTraceAnalysisResult {
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
      }
    };
  }, []);

  const resetAnalysis = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    dispatch({ type: "RESET_ANALYSIS" });
  }, [dispatch]);

  const handleFile = useCallback(
    async (file: File, analyzers: TabId[]) => {
      dispatch({ type: "START_UPLOAD", payload: { fileName: file.name } });

      try {
        const { trace_id } = await uploadTrace(
          file,
          analyzers.length ? analyzers : undefined
        );

        dispatch({
          type: "UPLOAD_SUCCESS",
          payload: { perfettoUrl: getPerfettoUrl(trace_id) },
        });

        if (pollRef.current) {
          clearInterval(pollRef.current);
        }

        pollRef.current = setInterval(async () => {
          try {
            const status = await fetchAnalysisStatus(trace_id);
            if (status.status === "done") {
              if (pollRef.current) {
                clearInterval(pollRef.current);
                pollRef.current = null;
              }
              const result = await fetchTraceResult(trace_id);
              dispatch({ type: "ANALYSIS_COMPLETE", payload: { data: result } });
              return;
            }

            if (status.status === "failed") {
              if (pollRef.current) {
                clearInterval(pollRef.current);
                pollRef.current = null;
              }
              dispatch({
                type: "ANALYSIS_FAILED",
                payload: { error: status.progress || "Analysis failed" },
              });
              return;
            }

            dispatch({
              type: "SET_STATUS",
              payload: { status: status.progress || "Analyzing..." },
            });
          } catch {
            // Ignore transient polling failures and try again next tick.
          }
        }, 2000);
      } catch (error) {
        dispatch({
          type: "ANALYSIS_FAILED",
          payload: { error: `Upload failed: ${error}` },
        });
      }
    },
    [dispatch]
  );

  return { handleFile, resetAnalysis };
}
