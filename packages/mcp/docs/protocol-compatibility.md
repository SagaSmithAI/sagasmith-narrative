# MCP protocol compatibility / MCP 协议兼容

| Boundary | MCP 2026-07-28 | Legacy initialized client |
| --- | --- | --- |
| Negotiation | Per-request protocol version, capabilities and `_meta` | `initialize` / `initialized` |
| Identity | Signed `sagasmith.auth-context/v2`, verified per request for `sagasmith-narrative-mcp` | Signed v1 context plus compatibility epoch |
| Catalog | Complete, sorted, private cache scope, 300 s TTL | Exposure-filtered; genuine changes may emit `tools/list_changed` |
| Cross-call guidance | Explicit opaque exposure handle with owner and TTL | Connection exposure adapter |
| Authority | Campaign, actor, role, phase, revision and idempotency remain server-owned | Same handlers and authority checks |
| Transport | stdio or Streamable HTTP | stdio or Streamable HTTP |

The exposure handle is a name, not a capability. It does not replace the signed
delegation or any server-side authorization check. HTTP connection pooling is
allowed only when no principal or campaign state is stored on the connection.

`exposure` handle 只是有 owner 与 TTL 的导航名称，不是 capability。每个 modern
请求都必须重新验证面向 `sagasmith-narrative-mcp` 的签名委托、campaign、角色、phase
与 revision。HTTP 连接可以复用，但不能把 principal 或 campaign 状态缓存到连接上。

## Upgrade / 升级

1. Deploy the merged Core auth-context v2 revision and configure the same signing
   secret in the trusted Host and Narrative MCP.
2. Upgrade the Agent client to SDK 2.x dual-era discovery, then deploy this MCP.
3. Verify modern stdio and HTTP contracts, stable catalog ordering, private cache
   scope, standard media/resource passthrough at the Host, and stale-revision recovery.
4. Move clients to 2026-07-28 per-request metadata before disabling the legacy
   adapter in a later release.

先部署 Core v2 与共享签名配置，再部署 dual-era Agent 和本 MCP；验证 modern
stdio/HTTP、目录确定性、private cache、Host 媒体转换与 stale revision 恢复后，
再逐步停用 legacy 适配器。

## Rollback / 回滚

Keep the legacy adapter enabled while rolling the Agent back. Do not downgrade a
database independently from the matching Core and MCP code. Restore a consistent
database backup only if a schema rollback is explicitly required.

回滚 Agent 时保留 legacy adapter；数据库必须与匹配的 Core/MCP 版本一起回滚，
不要单独降级数据库。
