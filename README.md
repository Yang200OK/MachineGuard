# MachineGuard

基于 NASA C-MAPSS FD001 模拟数据的设备健康分析平台，将 **LSTM 剩余运行周期预测、带出处的 RAG 问答、受控 Agent、人工审核与 MCP 接入**连接为可追溯的本地业务流程。

支持账号与访客访问、设备观测、知识检索、分析审批及外部客户端调用，目前可在本地运行。

## 核心功能

| 模块 | 已实现能力 |
|---|---|
| 设备工作台 | 100 台 FD001 测试设备、传感器曲线、指定观测周期预测、风险分组和本人历史 |
| 模型训练与推理 | 按发动机划分训练/验证集、200 轮 LSTM、best/last、SHA-256 清单和 CPU 推理 |
| 知识管理 | PDF/Markdown/TXT 上传、版本、解析/OCR、原文预览、审核、停用和知识快照发布 |
| RAG 问答 | pgvector 与 BM25 检索、RRF 融合、重排、受限指代、多轮问答、逐字引用核对和无证据拒答 |
| 受控 Agent | 五类版本化 Skill、白名单工具、模型选择工具、一键分析和可查看的处理轨迹 |
| 报告与任务 | 人工批准/拒绝、正式报告、PostgreSQL Checkpoint、Worker 重启恢复、幂等、取消与 SSE 补读 |
| 来源图谱 | 原文摘录关系、人工审核/撤回、最多两跳的有向路径、逐边出处回查 |
| MCP 接入 | stdio / Streamable HTTP、8 项受控工具、独立凭据、能力授权、撤销、预算和调用审计 |

维护任务为人工模拟核对任务。报告支持 Markdown/JSON/PDF 渲染；真实 API → Worker → OSS → 下载的自动化完整验证仍待补齐。

## 页面展示

以下为实际运行页面，涵盖各业务模块及主要操作界面。

### 登录与总览

登录页提供账号登录和访客入口；总览展示设备、模型、知识与任务状态。

![登录页面](docs/images/login.png)

![系统总览](docs/images/overview.png)

### 设备工作台

选择设备和观测周期，查看传感器趋势、执行 LSTM 预测，并核对风险分组与本人预测历史。

![设备工作台](docs/images/devices.png)

### 模型管理

查看模型版本、输入窗口、训练参数、评估指标与文件指纹。

![模型管理](docs/images/model.png)

### 知识管理

管理资料及其版本，查看解析结果和原文，审核资料并发布知识快照。

![知识管理](docs/images/knowledge.png)

<details>
<summary>查看原文阅读与知识发布页面</summary>

![原文阅读](docs/images/knowledge-reading.png)

![知识快照发布](docs/images/knowledge-publication.png)

</details>

### 智能助手与资料问答

从已发布知识中检索证据，生成带出处的回答；引用可展开核对原文。

![资料问答](docs/images/knowledge-chat.png)

![引用核对](docs/images/knowledge-citation.png)

### Agent 分析

固定设备和观测周期执行受控分析，查看模型、工具调用及处理结果，生成待审报告候选。

![Agent 分析](docs/images/agent-analysis.png)

![分析执行详情](docs/images/agent-details.png)

### 报告审核与正式报告

审核人查看候选及证据后批准或拒绝；批准后保存正式报告并关联模拟维护任务。

![报告审核](docs/images/report-review.png)

![正式报告](docs/images/report.png)

### 来源图谱

查看从原文建立并审核的实体关系，点击关系回查出处，沿有向关系查询证据路径。

![来源图谱](docs/images/source-graph.png)

### Skill 管理

查看五类受控 Skill 的能力范围、版本和工具约束。

![Skill 管理](docs/images/skills.png)

### 任务中心

查看异步任务的状态、处理详情与错误信息，并对支持取消的任务执行取消。

![任务中心](docs/images/jobs.png)

### 账户与 MCP 接入

查看当前身份、角色权限与会话；为外部客户端选择能力、签发独立凭据，查看调用记录并撤销接入。

![账户权限](docs/images/account.png)

![MCP 接入管理](docs/images/mcp-access.png)

## 架构与关键设计

```mermaid
flowchart LR
    UI[Vue 3 前端] --> API[FastAPI / 业务服务与权限校验]
    CLIENT[MCP 客户端] --> MCP[stdio / Streamable HTTP]
    MCP --> API
    API --> DB[(PostgreSQL / pgvector)]
    API --> MODEL[LSTM CPU 推理 / 固定模型包]
    API --> CLOUD[百炼 / 向量与重排]
    WORKER[独立 Worker / LangGraph] --> DB
    WORKER --> CLOUD
    WORKER --> OSS[私有 OSS / 原文与导出]
    API --> OSS
```

- **预测有固定输入。** 每次分析固定设备和截止周期，校验连续 30 周期窗口与模型指纹；RUL 数值由 LSTM 服务提供。
- **回答有固定来源。** 文档版本、知识快照和正文位置可核对；历史生成结论不作为新证据，来源失效会阻止新检索和相关管理动作。
- **模型调用受应用控制。** Skill 正文、工具名称和参数由代码校验；Agent 与 MCP 复用业务授权，不能凭模型文本获得 SQL、Shell 或任意文件访问能力。
- **报告需要人工决定。** 待审状态持久化，批准后保存正式报告；请求幂等与动作记录用于防止重复写入，重启恢复保留已完成步骤。
- **图谱关系保留出处。** 查询只沿已审核的有向关系展开一跳或两跳，不自动合并跨文档同名实体，也不从路径推导新因果关系。

## 模型结果

本次训练使用 seed=42，按发动机划分为 80 台训练、20 台验证；常量列筛选与标准化仅拟合训练数据。输入为 `30 × 18` 窗口，LSTM 为 2 层、hidden size 64，正式完成 **200 轮**且关闭 early stopping。按验证集原始 RMSE 选择第 20 轮 best，官方测试标签只用于离线评估。

| 模型 | 验证 MAE（3490 窗口） | 验证 RMSE | 官方测试末端 MAE（100 台） | 官方测试末端 RMSE |
|---|---:|---:|---:|---:|
| LSTM best | 18.517637 | 27.261069 | 15.464736 | 21.401106 |

指标均来自本次实际评估，使用原始、未裁剪标签，单位为运行周期。单次 seed 的结果和后期过拟合均保留记录，尚未进行多种子稳定性验证。

![200 轮训练曲线](docs/images/training-curve.png)

原始数值见[评估摘要](docs/evidence/model-evaluation.json)。仓库包含训练曲线与评估摘要，权重和训练恢复文件在本地生成。

## 技术栈

- 前端：Vue 3、TypeScript、Vite、Pinia、Element Plus、ECharts。
- 后端：Python 3.12、FastAPI、SQLAlchemy、Alembic、PostgreSQL 17、pgvector。
- 模型与检索：PyTorch LSTM、scikit-learn、BM25、RRF、百炼 Embedding / Rerank / 对话模型。
- 任务与接入：独立 Worker、LangGraph、SSE、阿里云 OSS、官方 MCP Python SDK。

具体依赖版本见 [pyproject.toml](pyproject.toml)、[requirements-api.txt](requirements-api.txt) 和 [frontend/package-lock.json](frontend/package-lock.json)。云模型名称与限额以 `configs/` 中当前配置为准。

## 本地运行

仓库包含源码、迁移、配置模板、自制知识资料和展示证据。首次检出需要自行准备 FD001 原始数据、生成或获得有效模型部署包、初始化 PostgreSQL/pgvector，并配置自己的百炼与私有 OSS。现有本机数据库、账号、知识索引和历史记录不随 Git 上传。

**新电脑或新检出：** 按[Windows 本地运行指南](docs/setup/local-run.md)准备依赖、数据、模型和数据库。指南提供安装和初始化步骤；全新电脑的完整复现尚未验证。

**已有开发环境：** 在项目根目录启动数据库，然后在三个 PowerShell 终端分别运行 API、Worker 和前端。API 与 Worker 终端需要继承已配置的百炼和 OSS 环境变量，命令见运行指南。

```powershell
# 项目根目录：启动已有的项目数据库。
.\scripts\postgres.ps1 start

# 终端一：启动 API。
& .\.venv-api\Scripts\python.exe -X utf8 .\scripts\serve_api.py

# 终端二：启动 Worker。
& .\.venv-api\Scripts\python.exe -X utf8 .\scripts\run_worker.py

# 终端三：启动前端。
Set-Location .\frontend
npm run dev
```

前端：`http://127.0.0.1:5173`；API 文档：`http://127.0.0.1:8000/docs`；MCP HTTP：`http://127.0.0.1:8000/mcp/`。

## 使用范围

- 使用 NASA 模拟数据和项目自制知识资料；未接入真实工业设备，不将结果表述为现场诊断或维修指导。RUL 的单位是运行周期，风险分组不等于故障概率。
- 本地流程与本机 MCP 已验证；公网 HTTPS、自动 OAuth、Docker 部署、CI 与备份恢复实测尚未交付。
- 报告本地渲染已检查，真实 OSS 自动化导出全链路验证仍有缺口。
- 图谱当前由人工摘录与审核，自动实体/关系抽取和别名合并未实现。
