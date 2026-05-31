<p align="center">
  <img src="assets/banner.png" alt="Hermes Agent" width="100%">
</p>

# Hermes Agent — Oxidane Fork

这是 [`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent)
的个人运维 fork。这里保留本地 Hermes 运行档案需要的改动，记录这些改动为什么存在，并且只在上游更新值得合入时做 forward-port。

上游的安装方式、产品介绍、通用配置说明请看上游仓库和文档；这个 README 只说明本 fork 自己维护的内容。

## 这个 fork 用来做什么

- 运行并维护本地 Hermes 部署 profile。
- 先部署实际需要的运维修复，不等待上游合并或重新发现同类问题。
- 让 fork-only 行为在以后更新上游代码前可以快速审计。
- 保持 fork 分支列表很小，不再保留旧的上游 PR/topic 分支堆积。

## 当前维护的功能

| 功能 | 对外表现 | 代表 commit |
|---|---|---|
| Goal 自动化 | `/goal` 可以跨会话压缩继续运行，并且根据具体完成证据停止，而不是只看到“完成/停止”字样就误判。judge timeout 可配置，方便稳定使用更强的主模型判断。 | `6f98baee4`, `b0b6a411` |
| 语音消息处理 | 语音会以“转写文本”的身份交给模型，而不是当成用户精确打字。prompt 明确提醒 ASR 可能出错，需要结合上下文理解；语音回复也可以回答待澄清问题。 | `2ab1f2c91`, `d813a03d` |
| Provider fast modes | `/fast` 把 Anthropic 的 `speed=fast` 和 ChatGPT/Codex 的 priority processing 分开控制；旧的 `service_tier=fast` 配置仍然兼容。这样切换 provider 时，不会因为同一个“fast”词义不同而误改路由语义。 | `a8b81172b` |
| 长会话压缩 | 压缩会生成结构化交接 checkpoint，保留当前任务、工作状态、文件、测试和剩余事项。请求形态也保持系统前缀/prompt-cache 行为一致，避免压缩后因为前缀差异导致上下文漂移。 | `e2e898f60`, `60a518780`, `39880e662` |
| Codex Responses replay | 辅助压缩沿用主 agent 的 Responses replay 形态，保留 tool call、function output、reasoning item、timeout/fallback，而不是把对话压平成容易丢信息的纯文本。 | `60a518780`, `39880e662` |
| 附件投递可靠性 | agent 输出本地文件或 `MEDIA:` 路径时，gateway 会尽量走平台原生上传，包含 archive 文件。附件无法解析或投递失败时，用户会收到明确提醒，生命周期 hook 也会把该轮视为失败。 | `27843b92d` |
| Web search 凭证池 | Tavily 支持多 API key、fill-first 选择和 cooldown；某个 key 达到 quota 后，真实请求会自然切到可用 key，不用后台探测浪费额度。 | `06abd9d49` |
| 后台 review 安全边界 | 后台 review 可以记录 memory、提出 skill 建议，但创建或修改 skill 必须经过批准，不会由非阻塞 review 线程静默写入。 | `dcd0d249` |

## 分支模型

- `origin/main` — 当前维护线，也是部署来源。
- `upstream/main` — 只在需要对比或 forward-port 上游变化时从 `NousResearch/hermes-agent` 拉取，不再镜像到 `origin`。
- 额外本地维护分支默认留在本地；只有在它有明确用途、且内容没有被 `main` 表达时，才需要记录并保留。
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
| `b776f462` | 明确 fork 分支契约和上游接入策略。 |
| `b0b6a411` | `/goal` judge 需要完成证据，并读取 timeout 配置。 |
| `d813a03d` | 把语音转写作为可能有误的上下文处理。 |
| `a8b81172b` | 区分 Anthropic Fast Mode 和 ChatGPT/Codex priority processing。 |
| `dcd0d249` | 后台 review 创建 skills 前必须经过批准。 |
| `27843b92d` | 附件发送失败会显式暴露。 |
| `39880e662` | 让压缩和 Responses replay 与当前上游 runtime 语义对齐。 |
| `06abd9d49` | Tavily 多 key 池保持可用。 |
| `6f98baee4` | goal 可跨 compression session split 保留。 |
| `60a518780` | 真实 Codex Responses replay 下长会话压缩更稳。 |
| `e2e898f60` | 为长会话压缩加入结构化交接 checkpoint。 |

## 更新上游代码时

把上游当作输入，不把它的大量分支当作本 fork 的公开分支面：

```bash
git fetch upstream --tags
```

对比 `origin/main` 和 `upstream/main`，只 forward-port 值得保留的变化；本地验证后，把新的 fork-only 行为记录到 `docs/local-changes/`。
