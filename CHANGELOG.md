# Changelog

All notable changes to perfetto-ai will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.1.0] — 2026-02-26

### Added
- **Jank Analyzer** — LLM-enhanced jank frame detection with call tree, critical path, and evidence SQL
- **Startup Analyzer** — cold/warm start phase breakdown with bottleneck identification
- **ANR Analyzer** — ANR event detection and main-thread blocking analysis
- **Memory Analyzer** — RSS/PSS trend analysis, GC pressure, OOM proximity scoring
- **Binder Analyzer** — slow IPC detection by interface name, main-thread blocking quantification
- **Multi-analyzer UI** — tab switcher (Jank | Startup | ANR | Memory | Binder) in left panel
- **AnalyzerPanel component** — unified score, key metrics, AI insights, issues, suggestions display
- **Evidence SQL** — every LLM conclusion includes runnable Perfetto SQL for manual verification
- **WebSocket bridge** — click jank frame → Perfetto UI jumps to timestamp
- **Resizable split layout** — drag splitter to adjust left/right panel width
- **Docker support** — `docker-compose up --build` for one-command deployment
- **Apache 2.0 license**
- **CI/CD** — GitHub Actions for PR validation (pytest + frontend build) and Docker image release on tag
