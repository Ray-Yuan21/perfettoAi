# perfetto-ai

**基于 LLM 的 Android 性能分析工具，由 [Perfetto](https://perfetto.dev) 驱动**

perfetto-ai 分析 Perfetto trace 文件，自动检测性能问题——jank 帧、应用启动慢、ANR、内存压力、Binder IPC 瓶颈——并提供 AI 驱动的根因分析，每条结论附带可验证的 SQL 证据。

---

## 功能特性

- **Jank 检测** — 基于 `present_type` 识别丢帧和延迟帧，正确处理 OEM 帧插值
- **启动分析** — 冷/热启动各阶段拆解：Application.onCreate → Activity.onCreate → 首帧渲染
- **ANR 检测** — 定位主线程阻塞原因（Binder、锁等待、IO、CPU 密集）
- **内存分析** — RSS/PSS 趋势、GC 压力、OOM 临近度
- **Binder IPC 分析** — 慢 IPC 调用、主线程同步阻塞，按接口名聚合
- **Perfetto UI 集成** — 点击任意 jank 帧直接跳转到嵌入式 Perfetto UI 的对应时间戳
- **SQL 证据** — 每条 AI 结论包含可运行的 Perfetto SQL 查询，供人工验证
- **运行时 LLM 配置** — 在前端 Settings 面板配置 API 地址、密钥和模型；模型列表自动发现

---

## 快速启动（Docker 一键部署）

单容器，一条命令：

```bash
# 1. 配置 LLM 凭据
cp .env.example .env
# 编辑 .env：
#   LLM_API_KEY=你的API密钥
#   LLM_API_ENDPOINT=https://api.openai.com/v1
#   LLM_MODEL_NAME=gpt-4

# 2. 构建并启动
docker compose up --build -d

# 3. 打开浏览器
open http://localhost:8000
```

> LLM 设置也可以在 UI 界面的 **Settings** 按钮中随时修改，无需重启容器。

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_API_KEY` | LLM 服务 API 密钥 | _（空）_ |
| `LLM_API_ENDPOINT` | LLM API 基础地址（兼容 OpenAI 格式） | `https://api.openai.com/v1` |
| `LLM_MODEL_NAME` | 模型名称 | `gpt-4` |
| `LLM_TIMEOUT` | LLM 请求超时时间（秒） | `300` |

### 数据持久化

上传的 trace 文件存储在 Docker volume `uploads` 中，容器重启数据不丢失。

```bash
# 停止
docker compose down

# 停止并清除上传的 trace 文件
docker compose down -v
```

---

## 本地开发

### 环境要求

- Python 3.10+，使用 [conda](https://docs.conda.io/) 管理
- Node.js 20+

### 后端

```bash
conda create -n perfetto python=3.11 -y
conda activate perfetto

cd backend
pip install -e ".[dev]"

cp config.yaml.example config.yaml
# 编辑 config.yaml，填写 llm.api_key

python run_server.py   # http://localhost:8000
```

### 前端

```bash
cd frontend
npm install
npm run dev     # http://localhost:5173，自动代理 API 到 localhost:8000
npm run build   # 生产构建 → dist/
npm run lint    # ESLint 检查
```

开发时后端和前端分别启动。前端 Vite 开发服务器会将 `/api` 请求代理到 `localhost:8000`。

### 测试

```bash
cd backend
conda run -n perfetto pytest tests/
conda run -n perfetto pytest tests/test_analyzer.py -k "test_name"  # 单个测试
```

---

## 使用流程

1. 在浏览器中打开应用（Docker: `http://localhost:8000`，开发模式: `http://localhost:5173`）
2. 点击右上角 **Settings** 配置 LLM 地址、API 密钥和模型
   - 模型下拉框会自动从你的 API 端点发现可用模型（通过 `GET /models`）
3. 拖拽 `.perfetto-trace` / `.pb` / `.pftrace` 文件到页面
4. 选择要运行的分析器（Jank、Startup、ANR 等），点击**开始分析**
5. Perfetto UI 立即加载 trace 文件；分析在后台异步进行
6. 分析完成后，左侧面板显示：
   - 性能评分（含 p95/最大帧时间）
   - AI 生成的洞察和问题排名列表
   - Jank 帧列表——点击任意帧跳转到 Perfetto UI 对应位置
   - 帧详情侧边栏：LLM 根因分析 + SQL 证据
7. 点击顶栏 **+ New** 按钮可以直接开始新的分析，无需刷新页面

---

## 架构概述

```
用户上传 trace
       │
       ▼
FastAPI 后端 ──► 存储文件，启动异步分析
       │
       ├─► 通过 Perfetto trace_processor 执行 SQL 查询
       │     （jank 帧、启动 slice、ANR 事件、
       │       内存计数器、Binder 事务）
       │
       ├─► LLM 对每帧/事件进行根因分析
       │
       └─► 结果缓存为 JSON
               │
               ▼
React 前端轮询 /api/status/{trace_id}
       │
       ├─► 左侧面板：各 analyzer Tab（Jank | Startup | ANR | Memory | Binder）
       │     评分、问题列表、AI 洞察、SQL 证据
       │
       └─► 右侧面板：Perfetto UI iframe
             点击帧 → WebSocket → Perfetto UI 跳转到对应时间戳
```

### API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/traces/upload` | POST | 上传 trace 文件，启动异步分析 |
| `/api/traces/{id}/status` | GET | 轮询分析进度 |
| `/api/traces/{id}` | GET | 获取完整分析结果 |
| `/api/traces/{id}/file` | GET | 下载原始 trace 文件 |
| `/api/jump` | POST | 跳转到 Perfetto UI 指定时间戳（通过 WebSocket 桥接） |
| `/api/settings` | GET | 获取当前 LLM 配置（密钥脱敏） |
| `/api/settings` | POST | 运行时更新 LLM 配置 |
| `/api/models` | GET | 从 LLM API 自动发现可用模型列表 |
| `/ws/bridge` | WS | Perfetto UI WebSocket 桥接 |

### 项目结构

```
├── Dockerfile              # 多阶段构建：Node 前端 → Python 后端单容器
├── docker-compose.yml      # 单容器部署 + uploads 卷
├── .dockerignore           # 排除 node_modules、__pycache__ 等
├── .env.example            # 环境变量模板
├── backend/
│   ├── run_server.py       # 服务入口
│   ├── pyproject.toml      # Python 依赖
│   ├── config.yaml         # 本地配置（可选，覆盖默认值）
│   └── perfetto_trace_analyzer/
│       ├── server.py           # FastAPI：API 路由 + 静态文件服务 + Perfetto 代理
│       ├── config.py           # YAML + 环境变量配置加载
│       ├── orchestrator.py     # 分析流程编排
│       ├── registry.py         # Analyzer 自动发现与注册
│       ├── trace_processor.py  # Perfetto trace_processor 连接
│       ├── models.py           # 数据模型
│       ├── llm_client.py       # LLM API 集成
│       ├── tools.py            # LLM 工具定义（Agent 分析）
│       ├── reporter.py         # 报告生成（JSON/HTML）
│       ├── scorer.py           # 性能评分
│       ├── base_analyzer.py    # 分析器抽象基类（共享 SQL 模板）
│       └── analyzers/
│           ├── jank.py         # Jank 检测 + LLM 根因分析
│           ├── startup.py      # 应用启动分析
│           ├── anr.py          # ANR 检测
│           ├── memory.py       # 内存分析
│           └── binder.py       # Binder IPC 分析
└── frontend/
    └── src/
        ├── App.tsx         # 主应用：上传、分析、Perfetto iframe
        ├── app.css
        ├── api/
        │   ├── client.ts   # API 客户端函数
        │   └── types.ts    # TypeScript 类型定义
        └── components/
            ├── SettingsPanel.tsx       # LLM 设置弹窗
            ├── ScoreBar.tsx           # 性能评分展示
            ├── IssueList.tsx          # 问题排名列表
            ├── JankFrameList.tsx      # 帧列表（点击跳转）
            ├── JankInsightsPanel.tsx   # AI 洞察面板
            ├── FrameDetailDrawer.tsx   # 帧详情侧边栏
            ├── AnalyzerPanel.tsx       # 通用分析器 Tab 内容
            └── Splitter.tsx           # 可拖拽分隔条
```

---

## Analyzer 列表

| Analyzer | 检测内容 |
|----------|----------|
| **Jank** | 丢帧/延迟帧，GPU/CPU 瓶颈，SurfaceFlinger 问题 |
| **Startup** | 冷启动各阶段耗时，类加载，ContentProvider 初始化 |
| **ANR** | 主线程阻塞，广播超时，Binder 死锁 |
| **Memory** | RSS 增长趋势，GC STW 暂停，OOM 临近度 |
| **Binder** | 慢 IPC 按接口名排名，主线程同步阻塞 |

---

## 添加新 Analyzer

1. 新建 `backend/perfetto_trace_analyzer/analyzers/your_analyzer.py`
2. 继承 `BaseAnalyzer`，实现 `name`、`sql_templates`、`prompt_template`、`analyze()`
3. 通过 `COMMON_SQL_TEMPLATES` 自动获得公共 SQL 查询（CPU 频率、线程状态、Binder、GC 等）
4. 在 `orchestrator.py` 中注册

---

## 贡献

见 [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md)。

## 许可证

Apache 2.0 — 与 [Perfetto](https://perfetto.dev) 保持一致。详见 [LICENSE](LICENSE)。
