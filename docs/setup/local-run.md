# Windows 本地运行指南

仓库不包含原始数据、模型权重、数据库、云凭据及业务记录。首次运行需准备这些资源；已有环境直接阅读第 6 节。全新电脑的完整安装与复现尚未验证。

## 1. 依赖与两类 Python 环境

训练需要 Python 3.12 与 `pyproject.toml` 中的依赖。请确认 Python、PyTorch 与硬件兼容。

API 使用项目专用 `.venv-api`，需要 API 依赖以及推理使用的 NumPy/Pandas/scikit-learn/PyTorch。选择一种方式准备：

- 已有兼容训练环境：用该 Python 执行 `-m venv --system-site-packages .venv-api`，复用已安装模型依赖，再安装 `requirements-api.txt`。
- 独立的新环境：使用 Python 3.12 创建 `.venv-api`，在其中安装本项目和 API 依赖。下列命令适用于这一路径，安装会下载依赖。

```powershell
# 在克隆后的项目根目录执行。
py -3.12 -m venv .venv-api
& .\.venv-api\Scripts\python.exe -m pip install -e .
& .\.venv-api\Scripts\python.exe -m pip install -r .\requirements-api.txt
```

前端需要 Node.js >=22.12.0，依赖版本由 `frontend/package-lock.json` 固定。第一次安装：

```powershell
Set-Location .\frontend
npm ci
# 仅在新检出且没有本地配置时复制；已有 .env 不覆盖。
Copy-Item -LiteralPath '.env.example' -Destination '.env'
Set-Location ..
```

## 2. FD001 数据与模型产物

从 [NASA 数据目录](https://data.nasa.gov/dataset/cmapss-jet-engine-simulated-data)获取 C-MAPSS 数据，并核对数据说明与使用条件。FD001 需要 `train_FD001.txt`、`test_FD001.txt`、`RUL_FD001.txt`；后者仅用于离线评估。

首次运行先复制数据配置模板，再将 `source_directory` 改为自己的数据路径。相对配置路径以项目根目录为起点。

```powershell
# 仅在没有实际配置时复制。
Copy-Item -LiteralPath '.\configs\data.example.json' -Destination '.\configs\data.json'
```

部署包包含 `model.pt`、`preprocessing.json`、`model_manifest.json`、`model_manifest.sha256`，不随源码上传。已有有效部署包时，将 `configs/business.json` 的 `bundle_directory` 指向其目录；没有部署包时执行训练。

新使用者自行训练时，使用准备好的训练 Python 执行以下入口。CPU 与 CUDA 需按实际环境显式选择；正式训练完成 200 轮，不使用 early stopping。

```powershell
# 示例使用上一步独立安装的 Python；已有训练环境可替换为其解释器。
& .\.venv-api\Scripts\python.exe -X utf8 .\scripts\inspect_dataset.py --split train
& .\.venv-api\Scripts\python.exe -X utf8 .\scripts\inspect_dataset.py --split test
& .\.venv-api\Scripts\python.exe -X utf8 .\scripts\prepare_dataset.py
& .\.venv-api\Scripts\python.exe -X utf8 .\scripts\train_model.py --device cpu
```

训练完成后，用输出的实际运行目录执行 `scripts/evaluate_model.py --run-dir <运行目录>`，再将 `configs/business.json` 的 `bundle_directory` 指向该目录下的 `deployment`。新训练生成新编号，不能直接套用原模型版本或保证逐位复现原指标。

## 3. 初始化 PostgreSQL 与 pgvector

仅用于没有 `.env` 和项目数据库的 Windows 新环境。安装脚本会下载并启动项目管理的 PostgreSQL 17.11，创建应用/迁移账号；已有环境不要重复执行。执行前核对本机 5432 端口没有被其他服务占用。

```powershell
& .\.venv-api\Scripts\python.exe -X utf8 .\scripts\install_postgres.py
& .\.venv-api\Scripts\python.exe -X utf8 .\scripts\install_pgvector.py
& .\.venv-api\Scripts\python.exe -X utf8 .\scripts\manage_database.py upgrade
& .\.venv-api\Scripts\python.exe -X utf8 .\scripts\manage_database.py check
& .\.venv-api\Scripts\python.exe -X utf8 .\scripts\manage_database.py create-admin --username admin
& .\.venv-api\Scripts\python.exe -X utf8 .\scripts\import_business.py
```

管理员密码交互输入，不回显，没有通用默认密码。数据库安装会生成根目录 `.env` 和 `local/` 中的实际本地记录，所以此安装路径不提前把 `.env.example` 复制为 `.env`。最新迁移为 `0017_mcp_access`；pgvector 安装仅适用于项目管理的 PostgreSQL 17.11。已有环境只在升级时执行迁移，并核对模型配置与登记。

## 4. 云配置

新检出先复制 OSS 配置与策略模板，已有实际配置不覆盖：

```powershell
Copy-Item -LiteralPath '.\configs\oss.example.json' -Destination '.\configs\oss.json'
Copy-Item -LiteralPath '.\configs\oss-ram-policy.example.json' -Destination '.\configs\oss-ram-policy.json'
```

在实际配置中填写自己的地域、Endpoint 与私有 Bucket，在策略中替换 `YOUR_BUCKET_NAME`，按[OSS 指南](oss.md)为自己的专用 RAM 身份配置限定权限。`configs/oss.json` 与实际策略已被 Git 忽略，密钥保存在环境变量中。

API 与 Worker 需要以下环境变量：

| 名称 | 用途 |
|---|---|
| `DASHSCOPE_API_KEY` | 百炼 Embedding、Rerank、对话和视觉解析 |
| `AccessKey_ID` | OSS 的专用 RAM AccessKey ID |
| `AccessKey_Secret` | 该 RAM 身份的 AccessKey Secret |

在 Windows 用户环境变量中设置实际值，不填写到 Git、前端 `VITE_` 字段或聊天截图。根目录 `.env` 只包含 `.env.example` 声明的数据库与服务设置，不能添加未知云配置字段。百炼需要能够使用 `configs/` 中配置的模型；缺权限、缺密钥或上游失败会明确报错。

## 5. 初始化知识与图谱

新数据库没有旧机器上的知识快照、会话、报告与关系。使用管理员在“知识与能力管理”中上传 `docs/knowledge/` 的八份自制 Markdown 正文，逐步完成解析、原文核对、审核与发布；目录 README 不作为知识文档上传。项目自制资料的适用范围见[知识目录](../knowledge/README.md)。

解析、Embedding 和问答由独立 Worker 执行。资料发布与查询会将相应文本发送到百炼并产生模型用量。图谱需从有效原文人工建立候选并审核，完成后才能查询相应路径。在“知识与能力管理”中核对资料状态，在“来源图谱”中核对关系及出处。

## 6. 日常启动已有环境

在项目根目录运行 `scripts/postgres.ps1 start`。分别打开 API、Worker 和前端三个 PowerShell 终端，前两个终端分别加载已保存的用户环境变量：

```powershell
$env:DASHSCOPE_API_KEY = [Environment]::GetEnvironmentVariable('DASHSCOPE_API_KEY', 'User')
$env:AccessKey_ID = [Environment]::GetEnvironmentVariable('AccessKey_ID', 'User')
$env:AccessKey_Secret = [Environment]::GetEnvironmentVariable('AccessKey_Secret', 'User')
```

API 终端执行：

```powershell
& .\.venv-api\Scripts\python.exe -X utf8 .\scripts\serve_api.py
```

Worker 终端执行：

```powershell
& .\.venv-api\Scripts\python.exe -X utf8 .\scripts\run_worker.py
```

前端终端执行：

```powershell
Set-Location .\frontend
npm run dev
```

三个终端保持运行；同一个端口不重复启动服务。打开 `http://127.0.0.1:5173` 并使用自己创建的账号登录；API 文档在 `http://127.0.0.1:8000/docs`。

## 7. 检查与停止

```powershell
Invoke-RestMethod 'http://127.0.0.1:8000/api/health'
# 在 frontend 目录执行，包含类型检查与构建。
npm run build
```

实际页面检查顺序：设备/周期与预测 → 原文与资料问答 → 分析轨迹 → 审核/正式报告 → 图谱路径 → 按需 MCP。云调用前核对所用文本、权限与额度，失败查看对应阶段错误，不生成替代数据。

停止 API、Worker 与前端时分别按 Ctrl+C；在项目根目录用 `scripts/postgres.ps1 stop` 停止项目数据库。报告渲染与真实 OSS 自动化导出全链路是不同检查；后者仍未完成完整验证。
