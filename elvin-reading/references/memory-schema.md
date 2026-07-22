# 本地记忆模型

## 项目与 Skill 分离

Skill 是稳定的工作方法和程序；阅读项目数据默认放在 `~/.elvin-reading/projects/`，用户明确指定时才改变。更新 Skill 时不得覆盖阅读数据。

初始化后的主要结构：

```text
<project>/
├── 00-阅读状态.md
├── 00-state/
│   └── project.json
├── 01-sources/
│   └── SRC-0001/
│       ├── original.<ext>
│       ├── extracted.md
│       └── metadata.json
├── 02-events/
│   └── reading-events.jsonl
├── 03-memory/
│   ├── memory-index.json
│   ├── memory-index.md
│   ├── open-questions.md
│   ├── 00-学习笔记.md
│   ├── 01-词汇笔记.md
│   ├── 02-语法笔记.md
│   └── 03-问题与理解.md
├── 04-sessions/
│   ├── checkpoints.jsonl
│   └── feedback.jsonl
└── 05-reuse/
    └── reuse-log.jsonl
```

`project.json` 是程序状态，`00-阅读状态.md` 供人和新 Agent 快速恢复进度。`03-memory/00-学习笔记.md` 是阅读者查看沉淀内容的入口。所有可读视图由程序生成，不手工维护。

## EvidenceSpan

每个事件必须至少包含：

- `source_id`：材料身份，不能用易变文件名代替。
- `location`：页码、章节、段落或 EPUB 章节。
- `quote`：足以验证解释的最小英文原文。

这三项共同构成证据锚点。没有锚点的想法可以在对话中讨论，但不能成为长期阅读记忆。

## ReadingEvent

`record` 追加一条不可变事件：

| 字段 | 含义 |
|---|---|
| `event_id` | 唯一事件编号 |
| `created_at` | UTC 时间 |
| `source_id` | 当前材料 |
| `type` | LEX/GRM/BKG/INF/QST/CON/OPI/SOL/CAS |
| `key` | 跨材料检索用的稳定名称 |
| `normalized_key` | 程序生成的规范化键 |
| `location` | 原文位置 |
| `quote` | 原文证据 |
| `question` | 使用者真实问法，可空 |
| `answer` | 当前可用解释 |
| `understanding` | 使用者复述或理解检查结果，可空 |
| `feedback_id` | 支撑 understanding 或 resolved 的真实反馈，可空 |
| `status` | open / partial / resolved |
| `aliases` | 同义词、缩写、词形或别名 |
| `related_to` | 被补充／修正／解决的旧事件 |
| `relation` | repeats / supports / revises / resolves / contrasts |

## MemoryItem

索引按 `type + normalized_key` 聚合同类事件，生成稳定 `memory_id`。它是可重建的物化视图，不是真源；真源始终是 JSONL 事件。

一个 MemoryItem 包含所有 occurrences，因此可以回答：第一次在哪出现、当前语境有什么变化、最新状态是什么、是否已被后续材料复用。

## key 与 aliases

- key 应跨材料稳定：`closure`、`non-restrictive relative clause`、`作者为什么反对静态类型`。
- 不要用页码、当前书名或完整原句作为 key。
- aliases 保存缩写、常见词形、同义表达，如 `closures, lexical closure`。
- 相同词形但意义明显不同，应使用更具体 key 或不同类型，避免错误合并。

## 状态与更新

- `open`：没有可用答案，等待后续证据。
- `partial`：已有可用解释，但仍待补充或等待使用者确认。
- `resolved`：已有可用解释；对于 LEX/GRM，还必须关联 outcome=success 的真实反馈。

首次解释词汇或语法时使用 `partial`。只有使用者明确说懂了、正确复述或通过检查后，才追加 `resolved` 事件。`understanding` 必须来自 `feedback_id` 对应的用户原话或忠实压缩，不能由 Agent 猜测。

更新采用“追加新事件 + 关系链接”，不覆盖旧事件。索引的最新状态来自最新 occurrence，历史仍可回看。

## 读者状态

`project.json.reader_model` 保存从真实反馈累计出的解释偏好：

- `feedback_count`：已记录的真实反馈数量；
- `signal_counts`：没懂、太抽象、已经理解等信号次数；
- `strategy_stats`：每种解释策略的 success / partial / failed / unknown 次数；
- `preferred_strategy`：当前证据下最有效的解释策略；
- `avoid_strategy`：失败次数最多、下次应谨慎使用的策略；
- `pending_strategy`：上一条反馈后准备使用、尚待用户评价的策略；
- `last_feedback`：最近一次真实反馈及结果。

读者状态只描述“怎样帮助这个使用者理解当前材料”。不要从少量反馈推断人格、能力标签或固定水平。

## FeedbackEvent

`feedback` 向 `04-sessions/feedback.jsonl` 追加不可变记录：

| 字段 | 含义 |
|---|---|
| `feedback_id` | 稳定反馈事件编号 |
| `created_at` | UTC 时间 |
| `source_id` | 反馈发生时的材料 |
| `location` | 页码、章节或当前对话 |
| `text` | 使用者真实原话 |
| `signal` | 规范化反馈信号 |
| `strategy` | 本轮采用的解释策略 |
| `next_strategy` | 当前反馈后准备采用的新策略，可空 |
| `outcome` | success / partial / failed / unknown |
| `obstacle_type` | 可选的主要障碍类型 |
| `note` | Agent 的最小必要说明 |

不修改旧反馈。每条用户消息最多追加一条反馈，它评价刚刚使用的策略。解释失败时在同一条记录中填写不同的 `next_strategy`，不要为了新策略再复制一条相同用户原话的 `unknown` 反馈。后续用户反应再评价该策略。

## 人类可读笔记

- `00-学习笔记.md`：总入口和各类数量；
- `01-词汇笔记.md`：语境义、用户问题、原文证据与理解状态；
- `02-语法笔记.md`：当前解释、原句、理解变化与确认状态；
- `03-问题与理解.md`：开放问题、背景、推理、概念、观点、方法和案例。

这些文件不展示 memory_id、event_id 等内部字段。内部索引继续负责审计与跨材料召回。

## 00-阅读状态.md

此文件集中呈现：项目目标、当前材料、阅读位置、下一步、开放问题数量、最近真实反馈、有效策略和应避免策略。逐条记忆仍由事件与索引管理。

## 阅读进度

`checkpoint` 把每次暂停位置追加到 `04-sessions/checkpoints.jsonl`，同时更新 `project.json` 中当前材料的最近位置。恢复阅读时由 Agent 自动读取；用户不需要记项目路径或手工维护进度文件。

`checkpoint --next-step` 同时保存下一次最小行动。新的 Agent 会话应先读取它，再决定是否询问用户。
