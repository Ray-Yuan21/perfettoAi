# 架构设计

本文档描述 perfetto-ai 当前的实现架构，重点说明前后端分层、分析流程和关键模块职责。

## 总览

项目采用“分层单体”的结构：

- 前端是 `React + Vite` 单页应用，负责上传 trace、轮询状态、展示分析结果，并通过 iframe 嵌入 Perfetto UI。
- 后端是 `FastAPI` 单体服务，负责文件接收、分析编排、LLM 调用、结果缓存，以及 Perfetto UI 代理和跳转桥接。
- Analyzer 通过注册表自动发现，统一由 `Orchestrator` 调度执行。

```text
浏览器
├─ React 应用
│  ├─ 上传 trace / 选择 analyzers
│  ├─ 轮询 /api/traces/{id}/status
│  ├─ 展示结果面板与 analyzer tabs
│  └─ 调用 /api/jump
└─ Perfetto UI iframe
   └─ 通过 WebSocket 接收跳转命令

FastAPI 后端
├─ routes/          接口层
├─ services/        应用服务层
├─ orchestrator.py  分析编排
├─ analyzers/       领域分析器
├─ presenters/      前端结果组装
└─ dependencies.py  共享运行时依赖
```

## 后端分层

### 1. 接口层

`backend/perfetto_trace_analyzer/routes/` 只负责协议边界：

- `traces.py`：上传、状态查询、结果查询、原始 trace 下载
- `settings.py`：LLM 配置读写、模型列表
- `catalog.py`：返回当前可用 analyzer 列表
- `bridge.py`：WebSocket 桥接与 `/api/jump`

这一层不直接做分析逻辑，只调用 service。

### 2. 应用服务层

`backend/perfetto_trace_analyzer/services/` 负责串业务流程：

- `trace_service.py`：保存上传文件、生成 `trace_id`
- `analysis_service.py`：启动异步分析、更新状态、预加载 trace
- `result_service.py`：读取结果并调用 presenter
- `catalog_service.py`：返回稳定的 analyzer 元数据

### 3. 领域层

领域核心位于以下模块：

- `orchestrator.py`：统一调度 analyzer、汇总报告、计算总分
- `registry.py`：自动发现 analyzer 并按 `name` 注入配置
- `base_analyzer.py`：分析器抽象基类与共享 SQL 模板
- `analyzers/`：具体分析器实现

当前 analyzer 包括：

- `jank/`：帧卡顿分析，已拆成独立子包
- `startup.py`
- `anr.py`
- `memory.py`
- `binder.py`

### 4. 展示组装层

`backend/perfetto_trace_analyzer/presenters/` 负责把领域结果转换成前端可直接消费的结构：

- `trace_result_presenter.py`：构建 `/api/traces/{id}` 返回体
- `jank_presenter.py`：生成 `jank_frames`，处理去重、排序、LLM 帧分析合并

通用序列化保留在 `reporter.py`，不再混入 jank 专属展示逻辑。

### 5. 基础设施与共享依赖

- `dependencies.py`：`AppServices` 容器，统一持有 `StateManager`、`TraceProcessorPool`、HTTP client、WebSocketHub`
- `state.py`：线程安全的分析状态与结果缓存
- `trace_processor.py`：Perfetto 连接池，包含同路径并发加载去重
- `llm_client.py`：封装 OpenAI/Anthropic/Gemini 风格接口与 JSON repair
- `proxy.py`：反向代理 `ui.perfetto.dev` 并注入 `static/bridge.js`

## 前端结构

前端代码位于 `frontend/src/`，当前已经按职责拆分：

- `App.tsx`：页面级装配与状态协调
- `state/appReducer.ts`：页面状态机
- `hooks/useTraceAnalysis.ts`：上传、轮询、结果拉取
- `hooks/useAnalyzerCatalog.ts`：从后端获取 analyzer 列表
- `components/ResultsPanel.tsx`：左侧结果面板
- `components/PerfettoPanel.tsx`：右侧 Perfetto / 上传区
- `components/AnalyzerPicker.tsx`：上传后 analyzer 选择
- `components/AnalyzerPanel.tsx`：非 jank analyzer 通用展示

前端运行时使用后端 `/api/analyzers` 作为 analyzer 元数据主来源，本地 `analyzers.ts` 仅作为 fallback。

## 核心流程

### 上传与分析

```text
上传文件
→ TraceService 保存文件并生成 trace_id
→ AnalysisService 异步启动分析
→ Orchestrator 通过 Registry 获取 analyzers
→ Analyzer 执行 SQL + LLM 分析
→ Scorer 计算整体得分
→ StateManager 缓存结果
→ 前端轮询 status，完成后拉取结果
```

### Perfetto 跳转

```text
前端点击帧/切片
→ POST /api/jump
→ WebSocketHub 广播消息
→ iframe 内注入的 bridge.js 接收跳转
→ 调用 Perfetto 内部 API 切换时间窗口并选中切片
```

## 设计原则

- Route 薄：接口层只处理请求和响应
- Service 清晰：业务流程集中在 service 层
- Analyzer 独立：每个 analyzer 只关心自己的 SQL 与分析逻辑
- Presenter 单独存在：前端需要的特殊组装不回流到通用报告层
- 共享依赖收口：避免模块到处直接引用全局对象

## 当前已知边界

- 结果主要缓存于内存与本地 JSON 文件，还不是持久化数据库架构
- Perfetto UI 通过反向代理和注入脚本桥接，属于运行时集成，不是源码级二次开发
- `perfetto-mcp-tmp/` 是实验目录，不属于主应用架构的一部分

## 相关文档

- 开发与运行方式：`README.md` / `README_CN.md`
- Analyzer 扩展指南：`docs/CONTRIBUTING.md`
- 提交与协作约定：`AGENTS.md`
