import { useEffect, useState } from "react";

import { FALLBACK_ANALYZERS } from "../analyzers";
import { fetchAnalyzers } from "../api/client";
import type { AnalyzerOption } from "../api/types";

export default function useAnalyzerCatalog(): AnalyzerOption[] {
  const [analyzers, setAnalyzers] =
    useState<AnalyzerOption[]>(FALLBACK_ANALYZERS);

  useEffect(() => {
    let cancelled = false;

    fetchAnalyzers()
      .then((items) => {
        if (!cancelled && items.length > 0) {
          setAnalyzers(items);
        }
      })
      .catch(() => {
        // Keep fallback analyzers when backend metadata is unavailable.
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return analyzers;
}
