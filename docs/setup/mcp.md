# MCP 接入指南

支持 MCP 的可信客户端可以调用 MachineGuard 的业务能力。日常网站问答、图谱查看和设备分析仍在原页面操作；只有需要外部客户端调用时才签发凭据。实现使用[官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk)；两种传输参考[协议说明](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)。

## 网站签发

1. 使用正式账号登录，点击右上角账号，进入“当前账号”，点击“MCP 外部接入”的“管理接入”。也可直接打开 `http://127.0.0.1:5173/account`。
2. 填写客户端名称、有效分钟，勾选所需能力。列表按当前角色生成；不勾选的能力不会开放。
3. 点击“签发独立凭据”。原值只展示一次，可以展开“客户端配置”，复制到可信客户端。关闭侧栏后清除原值，刷新或重复确认不会再返回原值；丢失后撤销再签发。
4. “本人凭据”可以撤销接入；“本人调用记录”按需展开，查看实际操作、结果、传输方式和请求编号。

凭据绑定签发时的登录会话，实际有效期为所选时长与该会话剩余时长的较小值。退出该会话、会话到期、账号停用或撤销凭据后，后续请求立即拒绝。浏览器只缓存结果待确认时的原签发参数及动作键，不保存 MCP 原值。签发响应丢失时，刷新后点击“确认原签发请求”，不会重复签发。

## 本机 Streamable HTTP

API 已挂载 `http://127.0.0.1:8000/mcp/`，保留末尾斜杠，避免重定向。请求使用 `Authorization: Bearer <独立 MCP 凭据>`；网站登录令牌与 MCP 凭据不能互换，URL 查询参数不接受凭据。

常见客户端配置结构如下，具体字段以目标客户端说明为准：

```json
{
  "mcpServers": {
    "MachineGuard": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp/",
      "headers": { "Authorization": "Bearer <独立 MCP 凭据>" }
    }
  }
}
```

本机 Streamable HTTP 协议已验证，未发布公网地址。当前身份接入为预配置独立 Bearer 凭据，没有自动 OAuth 登录、客户端动态注册或第三方身份提供者；需要这些流程的客户端须另行适配。

## 本地 stdio

stdio 由客户端启动本项目子进程，使用项目 Python 和脚本的绝对路径，并通过子进程环境提供凭据：

```json
{
  "mcpServers": {
    "MachineGuard": {
      "command": "C:\\path\\to\\MachineGuard\\.venv-api\\Scripts\\python.exe",
      "args": ["-X", "utf8", "C:\\path\\to\\MachineGuard\\scripts\\serve_mcp.py"],
      "env": { "MACHINEGUARD_MCP_TOKEN": "<独立 MCP 凭据>" }
    }
  }
}
```

搬迁项目后使用账号页返回的本机路径。stdio 不依赖 HTTP API 进程，但依赖相同数据库、业务模型包、项目配置及检索所需百炼环境；stdout 只承载协议，诊断输出在 stderr。HTTP 与 stdio 都逐请求验证身份，不接受模型正文、参数或 MCP 会话编号作为授权依据。

## 能力和参数

| 工具 | 用途与边界 |
|---|---|
| `list_engines` | 本身份设备列表，`offset` 分页，`limit` 最多20 |
| `get_engine_overview` | 明确提供 `engine_key`、`end_cycle`，返回该周期观测 |
| `get_sensor_trend` | 再提供 `start_cycle`、`columns`，最多6列、1000条观测，不开放未来周期 |
| `predict_rul` | 明确设备、周期和 UUID `idempotency_key`；调用原可信 LSTM 服务并保存本人记录，同键同参数只保存一次，同键不同参数明确冲突 |
| `get_prediction_history` | 固定设备和截止周期，仅本人预测，分页最多20条 |
| `search_maintenance_knowledge` | `query` 最长1000字符，`limit` 最多5；实际调用百炼向量与重排服务，返回有固定来源的候选证据，不代表结论已审核 |
| `search_evidence_paths` | 起点 `subject`、可选终点 `target`、`max_hops` 为1或2；按已审核方向查询，不推导新关系或故障 |
| `read_report` | `report_id` 为 UUID，只读本人或分配给当前审核人的正式报告 |

例如询问“查询风险规则到 critical 的两跳证据路径，并列出逐边出处”，授权了路径能力的客户端可调用：

```json
{"subject":"风险规则","target":"critical","max_hops":2,"limit":5}
```

资源 `machineguard://capabilities` 返回当前能力说明；授予报告读取后还可读取 `machineguard://reports/{report_id}`。提示词 `evidence-path-analysis` 是受限操作模板，不增加权限。未知工具、任意文件资源、SQL、Shell、文档管理和审核操作均不开放。

## 限额与排错

每凭据每分钟最多30次业务操作，其中知识云检索最多5次；能力、资源与提示词发现也计入操作预算。认证与初始化本身不产生业务结果。同一凭据的并发业务操作明确返回 `MCP_BUSY`，不会隐式排队或重试。调用超过120秒时不提交业务结果；这属于提交前预算校验，不承诺强制中断正在执行的外部请求。取消客户端等待也不能撤销已提交的预测，结果不明确时使用原预测键确认。

成功结果含 `request_id` 与 `data`；工具业务失败使用 `isError` 和结构化错误码，协议及认证失败明确报错。凭据、原问题和原结果不写入 MCP 审计正文，审计记录定位编号、操作、传输、耗时、输入/结果摘要及失败码。错误不生成替代数据。

新环境先按[本地运行指南](local-run.md)准备依赖、数据、模型和数据库。现有环境升级时执行 `scripts/manage_database.py upgrade`；当前数据库结构为 `0017_mcp_access`。主机、Origin、请求体和调用限额由 `configs/mcp.json` 控制，默认仅本机来源。
