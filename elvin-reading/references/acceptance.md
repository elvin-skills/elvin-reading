# Elvin-reading 验收标准

> 本文件供 Skill 维护者在修改或发布前使用。正常阅读流程不读取，也不要求使用者执行。

## 1. Skill 与命名

1. Skill 文件夹与 frontmatter 名称为 `elvin-reading`。
2. 用户入口统一为 `/elvin-reading`，UI 名称为 `Elvin-reading`。
3. 方法名称统一使用 `A+100`。
4. Skill 源文件中不存在旧名称、旧入口、旧脚本名或旧数据目录表述。
5. `quick_validate.py` 返回通过。

## 2. 首次阅读闭环

1. `projects` 能从默认根目录列出项目并按更新时间排序。
2. 初始化窄领域项目后生成 `00-阅读状态.md`、项目状态、反馈日志和检查点日志。
3. 导入第一份材料后获得 `SRC-0001`，原文副本和提取文本均存在。
4. `locate` 能用复制原句找到位置；PDF 优先返回页码标题。
5. 用户无需提供项目路径、source_id 或内部记忆类型即可开始阅读。
6. `checkpoint --next-step` 保存位置和下一次最小行动。
7. 新会话运行 `projects + status + source-info` 能恢复原文、位置、开放问题和下一步。
8. 首次成功写入有效阅读内容时，`record` 返回一次性学习笔记通知信号和入口；第二次及以后写入不再返回通知信号。
9. 恢复时严格区分 `last_location` 与 `next_step`；下一步指向另一材料或章节时，不得谎称上次已经读到那里。

## 3. 自适应反馈闭环

1. `feedback` 只记录用户真实原话，不把模板文字当反馈。
2. `not_understood` 或 `failed` 会进入读者状态，并影响下一次策略选择。
3. `understood + success` 会增加对应策略的成功记录。
4. `status` 返回最近反馈、首选策略、应避免策略和策略统计。
5. `00-阅读状态.md` 同步呈现这些状态。
6. 没有用户反应时允许记录 `unknown`，不能伪造 `success`。
7. 解释失败后，Agent 按 `adaptive-reading.md` 更换策略，不能只改写原答案。
8. 每条用户消息最多产生一条 feedback；失败时在同一条记录中保存不同的 next_strategy。
9. 同一句用户原话不能同时作为旧策略 failed 和新策略 unknown 重复计数。

## 4. 阅读记忆闭环

1. 第一份材料至少可记录一条 LEX、一条 GRM 和一条 QST。
2. 长期记忆必须包含 source、location、quote 和稳定 key。
3. 更新理解采用追加事件与关系链接，不覆盖第一次记录。
4. `CON / OPI / SOL / CAS` 只有能服务后续检索、比较或问题解决时才记录。
5. 无证据、机械摘要和脱离语境的单词表不能进入长期索引。
6. LEX/GRM 首次解释为 partial；resolved 必须绑定 outcome=success 的 feedback。
7. understanding 没有 feedback_id 时，程序拒绝写入新事件。
8. 自动生成 `00-学习笔记.md`、词汇笔记、语法笔记和问题与理解，且不暴露内部 ID。
9. 语法与开放问题优先用用户原问题作为标题；未绑定真实 feedback 的历史 understanding 不得显示为“你的理解”。

## 5. 跨材料调用闭环

1. 导入第二份同领域材料后获得 `SRC-0002`。
2. 已进入 Elvin-reading 阅读任务后，用户只询问材料 B 的当前问题、完全不提材料 A 或旧笔记时，Agent 仍会用 `current-source=SRC-0002` 主动检索第一份材料中的词汇、语法和问题。
3. 结果包含稳定 memory_id、旧 source、旧位置、旧原句和旧解释。
4. 当前材料不能冒充旧命中。
5. 回答显式使用“材料 A／材料 B”等标签，并包含真实标题与具体位置、旧完整最小原句、新完整原句、共同点和差异；不使用“以前的语境”泛称。
6. 只在回答实际使用旧记忆后写入复用日志。
7. 第二份材料追加同一 key 后，索引保留两份语境。
8. 知识命中但旧解释策略失败时，保留旧证据并更换解释策略。
9. 同形不同义或证据不足时明确不复用。

## 6. 失败条件

- 换 Agent 后只能依赖聊天记录，无法从本地恢复。
- 用户需要理解内部编号、目录或数据库才能继续。
- 用户说“还是没懂”后收到同结构的重复解释。
- Agent 没有得到反馈却记录“用户已掌握”。
- 首次解释尚未确认就把 LEX/GRM 标记为 resolved。
- 同一条“没懂”被重复写成两条 feedback，导致反馈统计膨胀。
- 只提醒“以前见过”，或把旧原句缩成短语，没有显示旧材料标题、具体位置、完整最小原句和解释。
- 更新覆盖第一次记录，无法观察知识演化。
- 用户复制原句后，Agent 未尝试定位就索要页码。
- 没有 EvidenceSpan 的内容进入长期记忆。
- 文档提取失败后仍创建成功 source。
- 每次问题都强制沉淀，或每次回答都要求用户确认保存。
- 首次写入有效学习内容后没有告知学习笔记入口，或后续每次答疑都重复提醒。
- 把下一步计划冒充上次实际阅读位置，或恢复时打开另一材料却没有交代真实断点。
- 已进入 Elvin-reading 阅读任务后，用户的问题与旧记忆高度重合，但因没有提到材料 A 或旧笔记而未触发本地召回。
- 跨材料回答用“以前的语境”或“旧语境”代替明确的“材料 A／材料 B”来源标签。
- 正常阅读过程修改了 Skill 文件、桥接目录或宿主配置。

## 7. 程序验证

```bash
python3 scripts/elvin_reading.py validate <项目目录>
python3 scripts/elvin_reading.py status <项目目录> --json
python3 scripts/self_test.py
python3 /path/to/skill-creator/scripts/quick_validate.py <skill目录>
```

`validate` 必须确认：JSONL 可逐行解析，所有 source、memory、event、checkpoint、feedback 和 reuse 引用有效，读者状态与反馈记录可恢复。

`self_test.py` 必须在隔离的临时目录完成发布回归，并至少覆盖：

1. TXT、Markdown、HTML、DOCX、EPUB 和带文本层 PDF 的导入与正文提取；
2. PDF 与 EPUB 的页码或章节定位；
3. 空文件、不支持格式和无文本层 PDF 失败后不创建 source；
4. `projects`、`source-info`、`locate`、`rebuild`、`status` 与 `validate`；
5. LEX、GRM、QST，未知／失败／成功反馈、next_strategy、确认后 resolved，检查点、跨材料召回、真实复用与追加历史；
6. 损坏 JSONL 与状态引用能被 `validate` 阻止。
7. 人类笔记自动生成，按词汇、语法、问题分类且不包含内部 ID；标题优先采用用户原问题，未确认理解不冒充用户反馈。
8. 旧版同一句反馈的 failed/unknown 重复对在有效视图中合并，但原始日志仍保留供审计。
9. 第一条有效阅读事件返回 `notify_learning_notes=true`，后续事件返回 false，且入口文件真实存在。
10. `ingest`、`source-info`、`status` 与 `recall` 返回稳定的“材料 A／材料 B”标签，供 Agent 直接展示。

## 8. 最小真实前向试验（约 8 分钟）

1. 在第一次干净会话中调用 `/elvin-reading`，导入含 3～5 句的材料 A。
2. 分别提出一个词汇问题、一个语法问题和一个暂时没有答案的问题；确认四个人类笔记文件已生成，且只收到一次入口通知。
3. 对其中一个解释先反馈“没懂”，再明确确认新的解释已经理解；检查策略发生变化，首次记录没有提前写成 resolved。
4. 保存阅读位置并关闭会话。
5. 在第二次干净会话中调用 `/elvin-reading` 继续同一项目，确认能恢复真实断点与下一步，再导入同领域材料 B。
6. 只针对材料 B 提问，不提材料 A、旧笔记或召回要求；至少让一个问题与材料 A 的词汇、语法或开放问题高度重合。
7. 确认回答主动显示材料 A／材料 B 的真实标题、具体位置、两边完整最小原句、共同点和差异；无可靠命中时不强行复用。
8. 运行 `status` 与 `validate`，确认只有实际采用的记忆写入复用日志，新增理解采用追加记录且项目校验通过。
