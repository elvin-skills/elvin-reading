# Elvin-reading

[![skills.sh](https://skills.sh/b/elvin-skills/elvin-reading)](https://skills.sh/elvin-skills/elvin-reading)

Elvin-reading 是一个面向真实英文材料的自适应阅读 Skill。它让 Codex、Claude Code 等本地 Agent 陪使用者持续阅读 PDF、Word、EPUB、HTML、Markdown 或纯文本，并把真实答疑沉淀为可跨材料调用的词汇、语法、问题与理解笔记。

## 它能做什么

- 直接从英文材料开始阅读，不要求先建立课程或填写表格。
- 根据“没懂”“太抽象”“举个例子”等真实反馈调整解释方式。
- 自动维护阅读位置、开放问题、读者状态和下一步行动。
- 在后续同领域材料中主动召回以前可靠的相关知识，并明确指出来自哪一份材料。
- 首次产生有效学习内容时建立人类可读的学习笔记，之后静默更新。
- 默认只把阅读数据保存在本地。

## 环境要求

- 支持本地 Skill 的 Agent，例如 Codex 或 Claude Code。
- Python 3。
- Agent 具备本地文件读写和运行命令的能力。
- 读取 PDF 时，系统最好装有 `pdftotext`、PyMuPDF 或 pypdf；扫描版 PDF 还需要 OCR 工具。

## 一键安装

适用于 Codex、Claude Code、Cursor、OpenCode 及其他支持 Agent Skills 的客户端：

```bash
npx -y skills add elvin-skills/elvin-reading -g --all
```

这条命令会把 `elvin-reading` 全局安装到该安装器支持的所有 Agent。若只想选择部分 Agent，使用：

```bash
npx -y skills add elvin-skills/elvin-reading -g
```

也可以让 Agent 执行：

```text
请从 https://github.com/elvin-skills/elvin-reading 安装 elvin-reading Skill。
```

## 开始使用

安装后，在新对话中输入：

```text
$elvin-reading
```

或直接把材料交给 Agent：

```text
使用 $elvin-reading 陪我阅读这份英文材料。
```

你只需要阅读和自然提问。遇到不懂的单词、短语、语法或句子时，选中或复制原文直接询问即可。

## 本地数据与隐私

阅读项目默认保存在：

```text
~/.elvin-reading/projects/
```

Skill 默认不会把阅读材料、笔记或学习记录上传到云端。请注意：你所使用的 Agent 或模型本身可能有独立的数据处理政策，这不由本 Skill 控制。

## 自检

在 Skill 目录中运行：

```bash
python3 scripts/self_test.py
```

## 仓库结构

```text
elvin-reading/
├── README.md
├── LICENSE
└── elvin-reading/
    ├── SKILL.md
    ├── agents/
    ├── references/
    └── scripts/
```

详细作用与使用说明：[飞书文档](https://my.feishu.cn/docx/CAwDdKWpPoIBhkxbwpQceCBYnTg)

## 开源许可

本项目采用 [MIT License](LICENSE)。你可以使用、复制、修改、分发和用于商业用途，但需要保留原版权与许可声明。发布过的 MIT 版本无法再撤回其既有授权。
