<p align="center">
  <img src="assets/banner.png" alt="Hermes Agent" width="100%">
</p>

# Hermes Agent — Oxidane Fork

这是 [`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent)
的个人运维 fork。这里保留本地 Hermes 运行档案需要的改动，记录这些改动为什么存在，并且只在上游更新值得合入时做 forward-port。

上游的安装方式、产品介绍、通用配置说明请看上游仓库和文档；这个 README 只说明本 fork 自己维护的内容。

## 这个 fork 用来做什么

- 运行并维护本地 `default` 和 `kurisu` 两个 Hermes profile。
- 先部署实际需要的运维修复，不等待上游合并或重新发现同类问题。
- 让 fork-only 行为在以后更新上游代码前可以快速审计。
- 保持 fork 分支列表很小，不再保留旧的上游 PR/topic 分支堆积。

## 本 fork 的主要改动

| 领域 | 本 fork 做了什么 |
|---|---|
| `/goal` 循环 | goal 状态可跨压缩/会话切分保留；judge 需要具体完成证据，不再只凭“完成/停止”字样结束；judge timeout 可配置，适配较慢的主模型判断。 |
| 语音消息 | 平台语音会先转写再交给模型；prompt 明确提醒转写可能有误，模型应结合上下文推断真实意图。语音回复也可以回应待澄清问题。 |
| 上下文压缩 | 保留 checkpoint-framed v2 compaction，并维护 Codex Responses replay/recovery 与上游 runtime 兼容修复。 |
| 消息网关 | 附件发送失败会显式暴露，不再被“文本发送成功”掩盖。Telegram goal、topic、compression 相关状态保持更稳定。 |
| Web/search 工具 | 保留 Tavily 多 key 池、冷却、crawl auth，以及合并后的 provider 文档。 |
| Review 安全边界 | 后台 review 可以提出 skill，但不能未经批准直接创建或修改 skill surface。 |
| Fork 维护 | README、分支策略和本地变更文档都明确说明：这是按本地需求维护的个人 fork，不是上游 PR 暂存区。 |

## 分支模型

- `origin/main` — 当前维护线，也是部署来源。
- `origin/upstream-main` — 上游 `main` 的镜像，只用于对比和 future forward-port review。
- 额外本地维护分支只有在有明确用途并记录到文档时才保留；当前 checkpoint worktree 是 `impl/v2-compaction-checkpoint`。
- backup 分支、临时 PR 分支、旧的上游 topic 分支都不是稳定 fork 状态的一部分。

准确策略见 [`docs/local-changes/branch-strategy.md`](docs/local-changes/branch-strategy.md)。

## Fork 差异记录在哪里

- [`docs/local-changes/README.md`](docs/local-changes/README.md) — 本地 fork 改动索引。
- [`docs/local-changes/local-branch-2026-05.md`](docs/local-changes/local-branch-2026-05.md)
  — commit 级说明、用户影响、验证结果和 forward-port 注意事项。
- [`docs/local-changes/branch-strategy.md`](docs/local-changes/branch-strategy.md)
  — remotes、分支清理策略、上游更新接入方式。

## 可参考的关键 commit

| Commit | 用途 |
|---|---|
| `e58803e6b` | 明确 fork 分支契约和上游对比分支。 |
| `abaa238fd` | `/goal` judge 需要完成证据，并读取 timeout 配置。 |
| `5fc9603ae` | 把语音转写作为可能有误的上下文处理。 |
| `1c4de266c` | 后台 review 创建 skills 前必须经过批准。 |
| `4cab4b729` | 附件发送失败会显式暴露。 |
| `9bb825941` | 让本地测试与当前上游 runtime 语义对齐。 |
| `e7c742a0c` | Tavily 多 key 池保持可用。 |
| `6f98baee4` | goal 可跨 compression session split 保留。 |
| `60a518780` | 真实 Codex Responses replay 下 compaction 更稳。 |
| `e2e898f60` | 保留 checkpoint-framed v2 context compaction。 |

## 更新上游代码时

把上游当作输入，不把它的大量分支当作本 fork 的公开分支面：

```bash
git fetch upstream --tags
git push --force-with-lease origin upstream/main:refs/heads/upstream-main
```

对比 `origin/main` 和上游镜像，只 forward-port 值得保留的变化；本地验证后，把新的 fork-only 行为记录到 `docs/local-changes/`。
