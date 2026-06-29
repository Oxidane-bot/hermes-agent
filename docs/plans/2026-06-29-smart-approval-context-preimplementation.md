---
title: "pre-implementation: Smart Approval lightweight conversation context"
status: implemented
date: 2026-06-29
type: design
scope: hermes smart approval
runtime_changes: true
---

# Smart Approval v2 预备实现：轻量会话上下文

## 状态

已在本地 fork 实现第一版轻量上下文：smart approval 会读取同一 turn 的确定性裁剪最近会话上下文，并保留语音转文字 wrapper 中的实际转录内容。

## 背景

当前 Hermes 的 smart approval 判定主要看到两类信息：

- 即将执行的命令 / 代码片段。
- guard 给出的局部风险描述。

这对单条危险动作足够保守，但对“用户最近明确要求做这件事”的场景理解不足。例如用户要求 agent 登录某台机器排查问题，随后模型发起 `ssh user@<known-ip>`；如果审批模型只看 `ssh + IP`，它可能把动作看成高风险甚至拒绝，而无法识别该 IP 是本轮任务中用户明确授权、历史上也常作为可信目标出现的机器。

Codex Guardian 的相关设计可以作为结构参考：把审批问题拆成两轴判断：

1. **动作本身风险**：命令 / 文件 / 网络 / 权限会造成什么影响。
2. **用户授权匹配度**：最近用户意图是否明确覆盖这个动作、目标和范围。

Hermes 可以先实现一个更轻量的版本：不额外调用总结模型，只在现有审批调用里附带一个确定性裁剪出来的最近会话上下文。

## 目标

- 让审批模型知道最近任务在做什么，尤其是用户明确授权的目标、机器、路径、profile、服务名、部署动作。
- 对 SSH、curl、systemd、部署、文件写入、外部网络调用等边界动作，区分“用户刚要求的动作”和“模型自己延展出的动作”。
- 保持审批路径轻量：不新增 LLM 总结调用，不引入长链路工具调用。
- 维持 fail-closed：上下文缺失、解析失败、模型输出不合规时不自动放行。
- 避免把 secrets、长日志、完整命令输出塞进审批 prompt。

## 非目标

- 不在第一版实现完整 Guardian 子代理或多轮 reviewer。
- 不让审批模型读取文件、查询网络或执行工具。
- 不用 LLM 生成 compact conversation context；第一版只做确定性裁剪。
- 不替代现有 hard guard / blocklist；硬性禁止仍应在 smart approval 前短路。

## 输入上下文 contract

审批调用建议从当前运行环境传入一个 `ApprovalContext`，字段尽量结构化：

```python
@dataclass
class ApprovalContext:
    session_id: str | None
    profile: str | None
    recent_user_messages: list[str]
    recent_assistant_intents: list[str]
    recent_tool_calls: list[ToolCallSummary]
    explicit_user_targets: list[str]
    trusted_target_hints: list[str]
    current_task_summary: str | None
```

第一版可不要求所有字段同时存在，优先级如下：

1. 最近 3 条用户消息。
2. 当前 tool call / command 的结构化摘要。
3. 最近 1-2 条 assistant 对用户要求的复述或计划摘要。
4. 最近同一会话中出现过、且由用户文本引入的 IP / hostname / repo path / profile / service name。

## 轻量裁剪预算

建议默认预算：

- `max_context_chars`: 6000
- `recent_user_messages`: 3
- `recent_assistant_messages`: 2
- `recent_tool_calls`: 4
- 单条消息最多 1200 字符。
- 单个 tool call 摘要最多 800 字符。
- tool result 默认不纳入；只纳入错误类别和短错误摘要。

裁剪原则：

- 优先保留用户最新授权语句，其次保留 assistant 计划，再保留工具调用摘要。
- 删除 API key、token、cookie、私钥、Authorization header、长 base64、长日志。
- 对命令输出只保留错误类型和最多 20 行尾部摘要。
- IP / hostname / path 不作为 secret，但要标明来源：`from_user_message`、`from_config`、`observed_only`。

## 配置草案

```yaml
approvals:
  smart_context:
    enabled: true
    max_context_chars: 6000
    recent_user_messages: 3
    recent_assistant_messages: 2
    recent_tool_calls: 4
    include_tool_results: false
    include_error_summaries: true
    include_user_target_hints: true
    redact_secrets: true
```

## 审批 prompt contract

审批模型不应只输出一个词。建议改成严格 JSON，方便记录和回退：

```json
{
  "risk_level": "low | medium | high | critical",
  "user_authorization": "none | weak | partial | strong | explicit",
  "target_trust": "unknown | observed | user_provided | configured_trusted",
  "outcome": "approve | deny | escalate",
  "rationale": "short reason"
}
```

判定要点：

- `risk_level` 只评估动作客观风险，不因为用户授权而降低风险。
- `user_authorization` 评估最近用户请求是否覆盖动作类型、目标、范围。
- `target_trust` 评估目标是否来自用户明确指定或配置可信来源。
- `outcome` 由风险和授权共同决定。

## 政策矩阵草案

| risk_level | user_authorization | target_trust | 建议 outcome |
| --- | --- | --- | --- |
| low | weak+ | any | approve |
| medium | partial+ | observed+ | approve 或 escalate |
| high | explicit | user_provided/configured_trusted | approve 或 escalate，取决于 destructive 程度 |
| high | none/weak | any | escalate/deny |
| critical | any | any | deny 或人工确认 |

补充规则：

- `rm -rf`, credential exfiltration, privilege persistence, unknown remote script pipe to shell 等硬风险仍应优先 deny/escalate。
- 用户明确说“你直接 curl / 真实 e2e / 重启 profile”时，相关 curl、Hermes chat、systemd profile restart 的授权强度应提高，但仍限制在同一 profile / 服务 / 目标范围内。
- 用户只说“看看日志”不代表允许部署、重启或改生产配置。

## SSH / IP 场景推演

### 应能批准或至少低摩擦升级

上下文：

- 用户最近说：“去我的这台机器 `<ip>` 上看一下 systemd 服务，必要的话重启 profile。”
- assistant 计划中复述了目标 IP 和服务名。
- 待审批命令：`ssh user@<ip> 'systemctl status hermes-profile-x'`

预期：

- `risk_level = high`（SSH 远程操作客观高风险）。
- `user_authorization = explicit`。
- `target_trust = user_provided`。
- `outcome = approve` 或按配置 `escalate`，不应因为“单看 SSH + IP”直接 deny。

### 应拒绝或升级

上下文：

- 用户只要求“看看本地日志”。
- 待审批命令：`ssh root@unknown-ip 'curl ... | bash'`

预期：

- `risk_level = critical`。
- `user_authorization = none`。
- `target_trust = unknown`。
- `outcome = deny`。

## 实现切片建议

1. 在审批模块增加上下文载体和上下文变量。
   - 新增 `ApprovalContext` / `ToolCallSummary`。
   - 新增 `set_current_approval_context(...)` 和 reset helper。
2. 在 agent 执行工具前构造 compact context。
   - 从当前 message history 裁剪最近用户消息。
   - 从 tool call args 生成摘要。
   - 从用户消息中提取 IP / hostname / repo path / profile / service name 作为 target hints。
3. 扩展 `_smart_approve(...)` 参数。
   - 保持无上下文时的兼容路径。
   - prompt 中明确禁止把上下文当成硬授权；它只是授权证据。
4. 从一词输出迁移到 JSON 输出。
   - 解析失败时 fail closed：`ESCALATE`。
   - 日志记录结构化结果，但不记录 secret 原文。
5. 增加测试。
   - SSH 到用户明确 IP。
   - SSH 到未知 IP。
   - `curl` 用户指定 endpoint。
   - `systemctl restart` 用户明确 profile。
   - 用户只要求查看日志时尝试部署 / 重启。

## 测试验收

- 单元测试覆盖 prompt 构造时的裁剪、redaction、target extraction。
- smart approval mock 模型输出 JSON 时能正确 map 到 approve/deny/escalate。
- 解析异常、空上下文、超预算上下文都不导致自动 approve。
- SSH/IP 用例能证明“用户明确授权 + 可信目标”与“未知目标”被区分。
- 日志不包含 token、Authorization、cookie、私钥片段。

## rollout 建议

1. 第一阶段：只生成 context 并记录 debug 指标，不改变审批 prompt。
2. 第二阶段：在非 destructive guard 上启用 JSON 判定和上下文 prompt。
3. 第三阶段：覆盖 SSH/systemd/deploy/curl 等高价值场景。
4. 第四阶段：根据误批 / 误拒日志调预算和策略矩阵。

## 开放问题

- trusted target hints 是否只来自用户最近消息，还是也允许 profile config / known hosts / project memory。
- 高风险但用户明确授权的命令，是直接 approve 还是统一 escalate 到人工确认。
- 是否需要记录 `approval_context_hash`，便于审计但不持久化完整上下文。
- 是否给不同 platform/profile 配置不同预算。


## 本次实现落点

- `tools.approval.build_recent_approval_context(...)`：从最近用户消息/assistant 摘要构建 6K 字符以内的 redacted context。
- `agent.turn_context.build_turn_context(...)`：每个 turn 绑定 approval ContextVar；工具线程通过现有 thread context propagation 继承它。
- `_smart_approve(...)`：prompt 改为 risk / user_authorization / target_trust 三轴，并要求 JSON 输出；解析失败 fail closed 到 `escalate`。
- 语音输入：沿用 gateway 已生成的 `[The user sent a voice message~ Here's what they said: ...]` 文本，不把审批上下文降级成音频文件占位符。

## 配套修复

同一提交还修复 context-overflow recovery 的误判：压缩后即使 message 数不变，只要 request token 粗估显著下降，也视为压缩有进展并重试，避免错误触发 `compression_exhausted` / gateway auto-reset。
