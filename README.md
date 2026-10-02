# Loom NPC / 织幕

让角色拥有自己的认知，让行动遵守世界的规则。

Loom NPC 是面向游戏 NPC 的轻量级 Python 运行时。模型提出结构化行动，运行时负责观察、记忆检索、知识与规则校验、执行、记录和回放。

首版带有一个可以直接运行的可视化实验室。默认使用确定性 mock，运行和测试无需网络、API key 或 GPU；也可显式启用 DeepSeek，让真实模型提出结构化行动，再由同一套规则校验和执行。

公开仓库：[KapokInHometown/LoomNPC](https://github.com/KapokInHometown/LoomNPC)。Python 包名为 `loom_npc`，CLI 名称为 `loom-npc`。

![灯港镇 Demo：秘密披露被规则拦截](docs/demo.jpg)

## 运行 Demo

需要 Python 3.9 或更新版本。从仓库根目录运行：

```bash
python3 -m loom_npc run
```

打开 <http://127.0.0.1:8765>。服务只监听本机；更换端口可使用 `--port 8766`。无需安装运行依赖，也无需构建前端。

### 灯港镇的一封信

你带着一封信来到灯港镇。守灯人 Mara 守着钥匙，信使 Ivo 知道一条暗道，守卫 Orin 留在灯塔。

在界面中依次尝试：

1. 让 Mara 透露灯塔秘密：未建立信任，行动被拦截。
2. 让 Mara 说出暗道：她不知道这件事，行动被拦截。
3. 把信交给 Mara：事件写入世界，信任和任务状态更新。
4. 再问灯塔秘密，领取钥匙，然后前往灯塔：合法行动依次执行。
5. 查看角色记忆、实际决策上下文和状态差异，导出 JSONL 并回放。

界面中的全局世界视图是开发者调试信息；NPC 只接收经过可见性过滤的上下文。问候、询问秘密、索要钥匙及文本输入使用当前选定的模型；交信、移动和知识边界测试直接提交固定的结构化行动。决策轨迹标明提案来源，文本输入用于提出行动，台词仍由已知话题模板生成。

## 接入 DeepSeek（可选）

启用真实模型需要网络和有效的 DeepSeek API key。密钥可由外部环境变量 `DEEPSEEK_API_KEY` 注入：

```bash
python3 -m loom_npc run --provider deepseek
```

也可以在仓库根目录自行创建 `API-Key.txt`，首行使用 `Deepseek-API-Key = 你的密钥`，然后显式指定该文件：

```bash
python3 -m loom_npc run --provider deepseek --api-key-file API-Key.txt
```

`API-Key.txt` 已加入 Git 忽略规则，不会自动读取。密钥只由本地 Python 服务持有，用于官方 HTTPS 接口的认证请求头；不会发送至浏览器或写入 trace。环境变量与文件同时配置时，显式文件优先。

默认模型为 `deepseek-flash`，可用 `--model` 更换；`--timeout 30` 设置网络超时。适配器使用标准库发送非流式请求，启用 JSON 输出、关闭思考模式，不自动重试。接口失败、空响应或截断会显示模型错误；无效 action 会显示解析失败，规则不允许的 action 会被拒绝。失败不会自动改用 Mock。参数依据见 [DeepSeek 接口文档](https://api-docs.deepseek.com/api/create-chat-completion/) 与 [JSON 输出说明](https://api-docs.deepseek.com/guides/json_mode/)。

手动运行一次真实决策并离线回放：

```bash
python3 -m loom_npc run --provider deepseek --api-key-file API-Key.txt --actor mara --input '你好，我刚到灯港镇' --trace traces/deepseek-hello.jsonl
python3 -m loom_npc replay traces/deepseek-hello.jsonl
```

上述 `run` 命令会产生真实 API 请求；`replay` 和 `eval` 不会。真实模型可能提出不同或无效的行动，单次请求成功不代表人设一致性或长期角色表现已通过评测。

## CLI

源码目录中可直接使用 `python3 -m loom_npc`。如需 `loom-npc` 命令，先在虚拟环境安装：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/loom-npc run
```

安装构建工具时可能需要网络；安装后的运行时没有第三方依赖。

```bash
python3 -m loom_npc validate
python3 -m loom_npc eval
python3 -m loom_npc run --actor mara --input '你好' --trace traces/hello.jsonl
python3 -m loom_npc replay traces/hello.jsonl
```

生成并回放完整任务：

```bash
python3 -m examples.quest > /tmp/loom-quest.jsonl
python3 -m loom_npc replay /tmp/loom-quest.jsonl
```

校验自定义世界可使用 `validate path/to/world.json`；用它启动 Demo 可使用 `run --world path/to/world.json`。可视化地图围绕随包提供的灯港镇场景设计，自定义世界应先通过 CLI 检验。

## 代码结构

| 目录 | 职责 |
| --- | --- |
| `loom_npc/core/` | 世界模型、观察、上下文、解析、执行与运行循环 |
| `loom_npc/models/` | 模型接口、确定性 mock、DeepSeek 适配器与集中提示词 |
| `loom_npc/verifier/` | 角色权限、位置、知识、秘密与任务规则 |
| `loom_npc/memory/` | 有来源的记忆与本地检索 |
| `loom_npc/replay/` | JSONL 导出与离线回放校验 |
| `loom_npc/evals/` | 固定场景评测及结构化结果 |
| `loom_npc/data/` | 随安装包分发的世界与评测 JSON |
| `loom_npc/integrations/` | 本地 HTTP 服务和静态 Demo |
| `examples/` | 完整任务示例 |
| `tests/` | 单元、回放与本地 HTTP 链路测试 |

详细的行为边界与扩展入口见 [架构说明](docs/architecture.md)。项目规范见 [AGENTS.md](AGENTS.md)。

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 -m loom_npc validate
python3 -m loom_npc eval
```

测试只访问本地文件和 loopback HTTP，不调用外部模型或网络服务。GitHub Actions 在 Python 3.9 与 3.13 上执行同样的检查。

## 当前边界

- 支持 `speak`、`move`、`give` 三种行动；台词绑定已知话题模板，尚不支持任意生成式自由文本的语义校验。
- 认知使用已知事实集合，记忆使用本地检索；尚未实现错误信念、记忆摘要或 embedding。
- 回放检验记录中的状态演进，不调用模型；它不是对 trace 来源的加密认证。
- 信件、钥匙与灯塔通行规则目前针对灯港镇场景实现，尚未抽象为通用任务配置系统。
- 本地 Demo 是单世界、单进程实验室，重置或重启会清空当前会话。需要保留时先导出 trace。
- 已提供可选 DeepSeek adapter；游戏引擎接入、多人会话和真实模型系统性评测尚未实现。
