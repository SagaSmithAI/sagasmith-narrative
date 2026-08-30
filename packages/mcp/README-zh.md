# SagaSmith Narrative MCP

[English](README.md) · [协议兼容、升级与回滚](docs/protocol-compatibility.md)

SagaSmith Narrative MCP 是长篇桌面叙事的 system-neutral 权威服务。它负责
campaign、actor、phase、revision、idempotency、随机流、原子结算与私有 NPC
conversation；Host/Agent 负责 LLM、上下文聚合、工具选择与受众决策。

长期战役既可以从完整 Module 开始，也可以从基础世界设定与少量开场 Scene
逐步扩展。`continuity_query` 的 actor-memory 模式会在分支与受众过滤后，返回
identity、motivational、semantic、episodic 四轨角色记忆，供 PC/NPC 连贯决策使用，
但不会替 NPC 选择意图或直接写入状态。facilitator-private 的 campaign design
显式保存 fronts、剧情 threads、clues 与角色 arcs。合理的 off-Atlas 行动通过签名的
扩展提案生成带 lineage 的 child episode Pack；无论起点是否为完整模组，新增内容
仍须经过 draft、证据审查、finalize、import、activate、checksum 与依赖校验，且不会
原地修改已 finalized 的父 Pack。普通 continuity 使用与 campaign、branch、principal、
actor、audience、query、limit、budget 绑定的可重启不透明 cursor，可跨过首 100 条结果
分页读取 Core 三条数据流，而不在 facade 中全量加载或重新排序。

## MCP 2026-07-28

- modern `tools/list` 完整、确定排序，并以 private scope 缓存 300 秒；Host 只把
  当前 system、phase 与任务需要的少量 facade/workflow tools 提供给模型，SagaSmith
  默认最多 16 项。16 是 Host 命中率策略，不是 MCP 协议限制。
- `exposure(search)` 支持过滤、limit 与 cursor；其显式 handle 有 owner 和 TTL，
  只记录导航选择，不是 capability，也不代替任何权限检查。
- Hosted 请求必须携带面向 `sagasmith-narrative-mcp` 的短期
  `sagasmith.auth-context/v2` 签名委托。服务端逐请求验证 requester、resource owner、
  acting host/character、allowed operations、audience、room turn、base revision 与 expiry。
- 禁止透传浏览器 token 或其他 audience 的 token。HTTP 连接可以复用，但不能缓存
  principal/campaign/session 权威状态。
- legacy initialize、连接 exposure 与 `tools/list_changed` 仅保留在迁移适配器。
- modern 请求逐次携带协议版本、client capabilities、trace context 与 `_meta`；可选
  `server/discover` 和 HTTP method/name 路由不会创建隐藏 session。

本 MCP 当前不声明 MCP Tasks extension；29 个公开工具都是有界同步操作。未来只有真正
长耗时的导入或渲染才应在能力协商后使用 Task，普通权威写入不能被包装成后台任务。

29 个公开工具都提供简洁工具说明、有界且带说明的输入、与结果对应的
`outputSchema`，以及四项 MCP 行为 annotation。成功结果同时保留标准 MCP text 和
经过 schema 验证的 `structuredContent`；可修复的执行错误返回包含 `code`、
`message`、`retryable`、`recovery` 的安全结构，未知 method/tool 与 schema 层请求错误
仍由协议层处理。契约测试覆盖 legacy/2026-07-28 × stdio/真实 Streamable HTTP，三个
完整 campaign fixture 还会按公开 schema 验证实际返回。

## 本地与 Hosted

本地默认使用 stdio；Streamable HTTP 与 stdio 调用相同 handler/schema/authority。
非 loopback HTTP 必须配置 `SAGASMITH_AUTH_CONTEXT_SECRET`。常用启动方式：

```powershell
uv sync --all-packages --all-extras
$env:SAGASMITH_NARRATIVE_MCP_TRANSPORT = "streamable-http"
$env:SAGASMITH_NARRATIVE_MCP_HTTP_HOST = "127.0.0.1"
$env:SAGASMITH_NARRATIVE_MCP_HTTP_PORT = "8770"
uv run sagasmith-narrative-mcp
```

## 验证

```powershell
uv run ruff check packages/domain packages/mcp
uv run pytest packages/domain/tests packages/mcp/tests
uv run python packages/mcp/scripts/regression_parallel_campaigns.py --output .runs/parallel
```

三个自有 fixture、10 项稳定只读 evaluation，以及 legacy/modern、stdio/HTTP、权限、
幂等、stale revision、并发与恢复测试都不依赖生产数据或付费服务。

现代请求传播 `traceparent`、`tracestate` 与 `baggage`。transport、discover/initialize、
catalog/exposure、tool 与 projection 指标只使用低基数标签，绝不以 user、campaign、run
或参数作为 metric label。
