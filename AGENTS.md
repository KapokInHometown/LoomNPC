# Loom NPC / 织幕

## 项目定位

Loom NPC（织幕）是面向 LLM 驱动游戏 NPC 的轻量级运行时。

它将模型输出转化为受游戏世界状态、角色主观认知、记忆和规则约束的结构化行为，并提供校验、执行、记录、回放和评测能力。

> A lightweight runtime for LLM-driven game NPCs.

命名约定：

- 项目名：`Loom NPC`（中文名：`织幕`）
- 仓库名：`loom-npc`
- Python 包名：`loom_npc`
- CLI 名称：`loom-npc`

## 边界

Loom NPC 关注：

- NPC 的有限认知、长期记忆和角色一致性
- LLM 输出到结构化 action 的转换
- action 的规则校验和执行
- NPC 决策 trace、replay 和 eval
- 可嵌入游戏原型的小型 runtime 核心

Loom NPC 不做：

- 完整游戏引擎
- 通用聊天前端
- 角色卡管理工具
- 大模型训练框架
- 大而全 Agent 平台
- 某个模型服务商 SDK 的封装
- 3D 渲染、动画、语音、多人在线或大型 UI 系统
- 仅靠 prompt 约束角色行为的对话 demo

功能准入标准：是否服务于**可信、可控、可测试的游戏 NPC 行为**。

## 设计原则

1. NPC 是游戏世界实体，不是孤立聊天机器人。
2. NPC 决策基于自己的 `Observation`、`BeliefState` 和 `Memory`，不是全局真相。
3. LLM 输出必须进入可解析、可校验、可执行的数据结构。
4. LLM 只能提出行动，不能直接修改世界状态。
5. 重要变化必须形成 `Event`，供 memory、replay 和 eval 使用。
6. 核心逻辑默认可在无网络、无 API key、无 GPU 环境中测试。
7. 关键决策默认可回放。
8. 核心层保持模型无关、引擎无关、UI 无关。
9. 优先实现小而稳定的组合模块，避免隐式复杂流程。
10. 解析失败、校验失败、模型失败和执行失败都必须显式表达。

## 核心领域模型

- `WorldState`：真实世界状态，游戏事实来源；只能通过 executor 或明确 mutation API 修改。
- `NPC`：非玩家角色，包含身份、人设、目标、位置、关系、记忆和可用行动。
- `BeliefState`：NPC 的主观世界模型；可以不完整、过时或错误，但必须受信息来源限制。
- `Observation`：NPC 当前可见、可听或可接收的信息；不得泄露隐藏信息。
- `Event`：游戏中发生过的事实记录；尽量 append-only。
- `Memory`：NPC 可检索的历史记忆；不是世界真相，应保留来源 event 或 trace。
- `Action`：结构化行动意图；必须解析、校验后才能执行。
- `Verifier`：行动校验器；返回结构化结果和失败原因。
- `Executor`：行动执行器；是修改 `WorldState` 的主要入口。
- `Trace`：决策轨迹；记录输入、观察、记忆、模型输出、校验、执行和状态变化。
- `EvalScenario`：行为评测场景；定义初始状态、输入、期望行为和判定标准。

## 标准运行循环

```text
player input / world event
        ↓
observe: 生成 NPC 可见 observation
        ↓
retrieve: 检索相关 memory
        ↓
build context: 组合 persona、goal、belief、observation、memory、allowed actions
        ↓
decide: LLM 或 mock model 生成 structured action
        ↓
parse: 解析并校验 action schema
        ↓
verify: 检查行动是否符合世界规则和 NPC 知识边界
        ↓
execute or reject: 执行合法行动，拒绝非法行动
        ↓
record: 写入 event、trace 和 state diff
        ↓
update: 更新 memory、belief、relationship 或 quest state
```

每一步都应有明确输入、输出和失败方式。不要把运行循环合并成不可测试的大函数。

## 推荐仓库结构

```text
loom_npc/
  core/           # 世界、NPC、事件、行动、运行循环等核心抽象
  memory/         # 记忆存储、检索、摘要、重要性和衰减策略
  models/         # LLMAdapter、MockLLM、本地模型或 API 模型适配器
  verifier/       # action 校验、知识边界校验、任务状态校验
  evals/          # NPC 行为评测框架和判定器
  replay/         # trace 记录、读取和回放工具
  cli/            # 命令行入口
  integrations/   # 可选游戏引擎、Web demo 或外部系统集成

examples/         # 示例世界、NPC 配置、评测场景和演示数据
tests/            # 单元测试、集成测试和回归测试
docs/             # 架构、设计决策、评测说明和使用文档
```

分层约束：

- `core/` 不依赖模型供应商、游戏引擎、UI 框架或网络环境。
- `models/` 通过 adapter 接入外部模型，不把供应商逻辑泄漏到核心层。
- `integrations/` 可以依赖外部引擎或 UI，但不得反向污染核心层。
- `examples/` 应小而完整，能验证核心行为。

## LLM Adapter

业务逻辑中不得直接调用模型 API。所有模型调用必须通过统一 adapter。

```text
LLMAdapter.generate_decision(context) -> ModelDecision
```

要求：

- 必须提供 mock adapter。
- 核心测试默认使用 mock adapter。
- 外部模型调用必须可关闭、可替换、可配置。
- 模型输出必须解析为结构化结果。
- 解析失败必须显式返回错误。
- 重试次数必须有上限。
- prompt/context 构造逻辑应集中管理并可测试。
- API key、访问令牌和个人密钥不得写入代码、示例配置或 trace。

## Action、Verifier、Executor

Action 是模型与游戏世界之间的边界对象。模型只能提出 action，不能直接改变世界。

Action：

- 使用显式 schema。
- 参数名稳定、语义清楚。
- 每种 action 有明确执行条件。
- 自由文本只能作为 action 字段，不得直接承担世界状态变更。
- 新增 action 类型时，同步补充 verifier、executor、测试和必要文档。

Verifier：

- 检查 actor、目标对象、地点、物品是否存在。
- 检查 actor 是否拥有执行权限或必要资源。
- 检查 actor 是否位于合理位置或满足互动条件。
- 检查 actor 是否知道该信息。
- 检查 action 是否违反任务状态、秘密边界或世界规则。
- 返回结构化失败原因。
- 校验失败不得修改世界状态。

Executor：

- 是修改 `WorldState` 的主要入口。
- 应尽量保持确定性。
- 应记录 event 或 state diff，便于 replay 和 eval。

## 记忆与认知

1. 区分短期上下文、长期记忆、主观认知和真实世界状态。
2. 记忆条目应包含来源、时间、相关角色、相关地点和重要性。
3. 记忆检索结果应能被 trace 记录。
4. 记忆摘要不得覆盖原始事件记录。
5. 不要默认使用全局历史；应按 NPC 可见性和相关性过滤。
6. NPC 可以因错误记忆做出错误判断，但 executor 必须保护真实世界规则。
7. 默认实现应能在无外部 embedding 服务时运行。

## Trace 与 Replay

重要决策应记录：

- trace id
- 逻辑 tick 或时间戳
- actor id
- 输入事件
- observation
- belief 摘要
- 检索到的 memory id 或摘要
- prompt 版本或实际 prompt
- 模型原始输出
- 解析后的 action
- verifier 结果
- executor 结果
- state diff
- 错误信息

要求：

- trace 优先使用 JSONL 等便于 diff 的文本格式。
- replay 不依赖真实模型调用。
- trace 不得包含密钥、完整外部凭证或敏感个人信息。

## Eval

重点评测类型：

- `Persona Consistency`：NPC 是否保持人设和说话风格。
- `Secret Leakage`：NPC 是否过早透露隐藏信息。
- `Knowledge Boundary`：NPC 是否说出自己不知道的信息。
- `Memory Recall`：NPC 是否记得关键历史事件。
- `World Consistency`：NPC 输出是否和世界状态矛盾。
- `Action Legality`：NPC 是否提出非法行动。
- `Quest Invariant`：NPC 是否破坏任务流程或剧情约束。

要求：

- eval 场景应可读、可复现、可在命令行运行。
- eval 应支持 mock model 和固定输入。
- 需要真实模型的 eval 必须显式标记为可选，不得默认运行。
- 新增关键行为时，应补充对应 eval 或回归测试。

## CLI 与配置

CLI：

```text
loom-npc run
loom-npc eval
loom-npc replay
loom-npc validate
```

要求：

- CLI 入口保持轻量，核心逻辑放在可测试模块中。
- CLI 输出应说明成功、失败和失败原因。
- 示例命令必须在文档中保持可运行。
- 示例世界、NPC、评测场景优先使用 YAML 或 JSON。
- 配置字段命名应明确。
- 配置加载应有 schema 校验或明确错误提示。
- 示例配置不得包含真实密钥、个人隐私或不可公开数据。

## 测试要求

默认测试必须能在无网络、无 API key、无 GPU 的环境中运行。

最低覆盖：

- 核心数据结构
- action parser 的成功和失败用例
- verifier 的合法行动和非法行动
- memory retrieval 的 mock 或简单实现
- replay 读取固定 trace
- eval 运行固定场景并返回结构化结果
- context builder 不泄露隐藏信息

测试分层：

```text
unit tests:      快速、确定、无外部依赖
integration:     可选，允许接入文件系统或本地服务
live model eval: 可选，必须显式启用，不得默认运行
```

## 代码风格

1. 使用类型标注。
2. 优先写小函数和小类。
3. 避免把 prompt、状态修改、模型调用、校验逻辑写在同一个函数里。
4. 错误处理要显式，避免裸 `except`。
5. 公共函数和核心类应有简洁 docstring。
6. 命名贴近领域概念，例如 `WorldState`、`BeliefState`、`ActionVerifier`、`EventLog`。
7. 依赖项保持克制；新增依赖要说明用途。
8. 不要提交与任务无关的大规模格式化改动。
9. 保持代码、测试、文档同步。

## 编码 Agent 规则

1. 修改前阅读相关代码和文档，不要凭空假设项目结构。
2. 优先做小而确定的改动，不要无请求重写架构。
3. 不要实现超出当前任务边界的大功能。
4. 不要把模型供应商、游戏引擎或 UI 框架耦合进核心模块。
5. 不要让外部模型调用成为默认测试路径。
6. 不要把密钥、令牌、私有路径或个人信息写入仓库。
7. 新增功能必须检查 schema、verifier、trace、eval、测试和文档是否需要同步更新。
8. 修复 bug 时优先补充回归测试。
9. 无法运行测试时，说明未运行的命令和原因。
10. 阶段计划、临时调研、任务备忘和一次性实验结论不得写入项目规范。

## 新增功能检查清单

1. 是否服务于可信、可控、可测试的 NPC？
2. 解决了哪个明确失败模式？
3. 是否破坏模型无关性？
4. 是否让核心模块依赖外部服务？
5. 是否需要同步更新 action schema、verifier、executor 或 eval？
6. 是否能用 mock model 测试？
7. 是否会让 NPC 获取不该知道的信息？
8. 是否能被 trace/replay 观察到？
9. 是否应该放在 integration 或 example，而不是 core？
10. 是否已有测试或文档解释行为？
