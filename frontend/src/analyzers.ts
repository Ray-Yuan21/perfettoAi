import type { AnalyzerOption } from "./api/types";

export const FALLBACK_ANALYZERS: AnalyzerOption[] = [
  { id: "jank", label: "Jank", description: "帧丢失 / 卡顿分析" },
  { id: "startup", label: "Startup", description: "应用启动耗时" },
  { id: "anr", label: "ANR", description: "无响应检测" },
  { id: "memory", label: "Memory", description: "内存泄漏 / 占用" },
  { id: "binder", label: "Binder", description: "IPC 延迟" },
];

const ANALYZER_LABELS = Object.fromEntries(
  FALLBACK_ANALYZERS.map((analyzer) => [analyzer.id, analyzer.label])
) as Record<string, string>;

export function getAnalyzerLabel(name: string): string {
  return ANALYZER_LABELS[name] ?? name;
}
