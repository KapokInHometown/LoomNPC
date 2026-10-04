# 真实模型评测

显式运行 `eval-live` 可以取得真实 adapter 的结构化行为指标和逐轮证据。默认 `eval`、网页评测、单元测试与 CI 仍使用确定性离线流程。实现入口位于 `loom_npc/evals/live.py`，供应商选择与凭据加载位于 CLI；评测函数必须接收 adapter，没有默认模型。

## 运行

需要网络和有效的 DeepSeek 凭据。由外部环境注入 `DEEPSEEK_API_KEY` 后运行：

```bash
python3 -m loom_npc eval-live --provider deepseek --model deepseek-flash --timeout 30 --repeats 3 --output traces/live-first
```

也可显式读取已被 Git 忽略的本地密钥文件：

```bash
python3 -m loom_npc eval-live --provider deepseek --api-key-file API-Key.txt --output traces/live-file-key
```

结果目录必须尚不存在，以保护已有证据。`--repeats` 必须为正整数，默认 1；每次重复都会重建世界。随包场景每轮共有 8 个步骤，因此请求次数为 `8 × repeats`。每步最多调用一次，不重试、不改用 Mock；某步失败后继续运行余下固定输入，以记录失败后的实际表现。超时是每步的模型网络超时。

退出码 0 表示所有场景轮次均完成：状态目标全部满足、没有模型/解析/执行错误且回放通过。规则拒绝可以满足边界场景。退出码 1 表示有未完成轮次或配置、凭据、文件错误；评测轮次未完成仍保存报告。命令参数缺失时退出码为 2。

## 场景和目标

随包场景分别检查合法行动、秘密保护、交信前的钥匙限制、无钥匙的灯塔限制，以及完整送信任务。边界场景允许模型选择其他合法行动，只检查受保护的最终状态；拒绝类型反映模型实际提出过哪些非法行动。问候场景只检查产生一次合法状态推进，不给台词风格打分。任务场景检查交信、交钥匙、持有钥匙、获知秘密与到达灯塔，不比较模型输出字符串。

用 `--scenarios path/to/live-suite.json` 指定自定义场景。格式如下：

```json
{
  "scenarios": [
    {
      "id": "deliver_letter",
      "name": "把信送到守灯人手中",
      "kind": "task",
      "steps": [
        {"actor_id": "player", "input": "把我持有的失落的信交给玛拉。"}
      ],
      "goals": {
        "quests.letter_delivered": {"equals": true},
        "actors.mara.inventory": {"contains": "letter"},
        "actors.player.inventory": {"not_contains": "letter"}
      }
    }
  ]
}
```

场景 id 必须唯一。`kind` 为 `smoke`、`boundary` 或 `task`；只有 `task` 纳入任务完成率。步骤仅允许 `actor_id` 和 `input`，角色必须存在，输入必须为非空文本。目标使用世界快照的点分路径，每个目标只有一个操作：标量 `equals`，或列表 `contains` / `not_contains`。比较值为字符串、布尔值或整数；路径与字段类型会在任何模型调用前校验。世界固定为随包的灯港镇初始状态。

目标只交给评测器，不发送给模型；模型仅接收运行时构建的角色可见 context。不要将凭据或个人隐私写入场景输入。

## 指标

| 字段 | 含义 |
| --- | --- |
| `model_calls` | 本次评测走 adapter 的总步骤数 |
| `model_outputs` | 收到 action 原始输出的步骤数；无效 JSON 也计入 |
| `model_errors` | 模型调用、响应或 adapter 故障的次数 |
| `parse_successes` / `parse_failures` | action schema 解析成功 / 失败次数 |
| `parse_success_rate` | 解析成功数 / 收到输出数；全是模型错误时为 `null` |
| `parsed_per_call_rate` | 解析成功数 / 全部调用数，包含模型故障的影响 |
| `verifier_checks` | 已解析并进入规则校验的次数 |
| `verifier_rejections` | 按 `SECRET_LOCKED` 等稳定规则代码聚合的拒绝次数 |
| `executed_actions` / `execution_errors` | 执行成功 / 执行故障次数 |
| `status_counts` | 按 trace 状态聚合的次数 |
| `completed_runs` / `total_runs` | 完成的场景轮次 / 全部场景轮次 |
| `task_completion` | 仅 `task` 的完成数、总轮次和比例；无任务时比例为 `null` |

各项均提供原始计数，比例范围为 0 到 1。`goals_met` 仅表示最终状态目标满足；`completed` 还要求该轮没有模型、解析、执行错误并且回放通过。例如模型报错时世界保持不变，秘密保护目标可能仍满足，但该轮不会被算作完成。

这组统计区分模型提案、运行时拒绝和任务结果。运行时成功拦截秘密并不能证明模型主动遵守秘密约束；解析率高也不能证明模型完成任务。重复次数是样本数，不是确定性承诺，真实模型可能在相同输入下返回不同行动。

## 证据与回放

`result.json` 保存运行 UUID、UTC 起止时间、配置、完整场景、重复次数、聚合指标和逐轮结果。每轮保存 `scenario-NNN-repeat-NNN.jsonl`，文件编号对应报告场景顺序。每个步骤的 `trace` 含相对文件名、从 1 开始的行号和 trace id；trace id 在各轮内重新编号，因此应使用完整引用定位。

逐步 provider、model、prompt version 在有 `model_request` 时取自实际请求记录，`provenance_source` 为 `request`。模型故障没有完整请求记录时使用运行配置并标记 `configuration`；这表示预定的模型与提示词，不能证明请求已被供应商接收。context 内的 `loom-mock-v1` 不作为在线提示词版本。请求参数和实际 messages 可在成功取得完整响应的 trace 中审查。

```bash
python3 -m loom_npc replay traces/live-first/scenario-005-repeat-001.jsonl
```

回放不调用模型，重算规则、执行和状态演进；模型或执行故障只核对失败结构与未改状态，不重现外部故障。每步 trace 立即写入并刷新，完整报告在全部场景结束后写入；中途取消可能只有部分 trace，没有完整报告。

结果默认放在已被 Git 忽略的 `traces/` 内。密钥只在 adapter 的认证请求头中使用，结果不记录密钥、认证请求头或供应商错误正文。原始输入和全局调试状态仍属于开发者数据。

## 验证边界

默认测试用脚本化 adapter 和模拟 DeepSeek HTTP 响应覆盖成功、解析失败、规则拒绝、模型错误、执行错误、任务失败、重复隔离、trace 回放、凭据隔离及 CLI 退出码，不发送外部请求。真实模型分数只有显式运行上述在线命令后才能取得。

首版评测支持当前的 `speak`、`give`、`move` 和登记话题模板；尚不评价自由生成台词、自然语言中的隐性泄密、人设一致性或长期记忆质量。
