# 任务：为 cbhcli 新增一次性非交互执行入口 `cbhcli exec`（headless 模式）

> 使用方式：将本文件完整发给执行 Agent（cbhcli 更新助手），作为功能实现的任务提示词。
> 对标产品：Claude Code 的 `claude -p "..."`、Codex CLI 的 `codex exec "..."`。

## 一、目标

在终端一次性下发任务 -> AI 自动完成 ReAct 工具调用循环 -> 输出最终结果 -> 进程退出。
不进入交互式聊天界面，不渲染欢迎面板，不启动输入框。用于 shell 脚本、CI/CD、管道集成。

## 二、CLI 接口规范

用法：

```
cbhcli exec [选项] [PROMPT]
echo "任务..." | cbhcli exec          # stdin 管道
cat task.md | cbhcli exec -           # "-" 显式表示从 stdin 读
```

参数表：

| 参数 | 说明 | 默认 |
|------|------|------|
| PROMPT（位置参数） | 任务描述；缺省且 stdin 非 TTY 时自动读 stdin；都没有则报错打印用法退出 2 | - |
| --agent NAME | 使用指定 Agent（不存在则报错并列出可用 Agent） | 上次激活的 Agent |
| --model NAME | 本次临时使用指定模型，原地切换组件，**禁止写入持久化配置**（进程退出即失效） | Agent 当前模型 |
| --mode readonly\|standard\|auto\|yolo | 权限模式，argparse choices 校验 | yolo |
| --max-turns N | ReAct 循环最大轮次保护 | 999 |
| --output-format text\|json | text=只输出最终回答；json=输出单条 JSON（见第五节结构） | text |
| --quiet, -q | 抑制工具执行过程/思考过程输出，只打印最终回答（管道友好） | 关 |
| --continue, -c | 续接该 Agent 最近一次历史会话后执行（对齐 claude -c） | 关 |
| --resume SESSION_ID | 恢复指定历史会话后执行 | - |
| --no-save | 本次会话不写入 history（默认保存，供 --continue/-c 续接） | 保存 |
| --cwd PATH | 启动前 chdir 到指定目录 | 当前目录 |

## 三、技术实现路线（基于现有代码，精准复用）

1. **cli.py 入口分发**：仿照现有 `web` 子命令的写法（`unknown_args[0] == 'exec'` 分支），
   argparse 加 `--output-format`/`--agent` 等参数，转发给新模块。
2. **新文件 `cbhcli_pkg/core/exec_runner.py`**：`ExecRunner` 类承载全部逻辑，cli.py 保持轻量。
3. **复用 CBHCLIApp，跳过交互主循环**：
   - 构造 `CBHCLIApp()`（自动完成组件初始化），**不调用 `app.run()`**；
   - `app._load_agent(agent_name, do_index=False)` 加载指定 Agent；
   - `--model` 时 `app.global_config.get_model(name)` 取配置 -> `app.switch_model(cfg)` 原地切换；
   - `app.set_permission_mode(mode)` 设置权限模式（复用，系统提示注入自动生效），
     随后禁用交互类工具（见第四节，复用注册表 disabled_tools 机制）；
   - `--continue`/`--resume` 时从 `app.session_history` 恢复消息到 `app.session`；
   - 调 `app._handle_ai_request(prompt)`（复用它已有的压缩检查+UserPromptSubmit/Stop 钩子链，
     不要直接调 ai_handler.process_request 绕过钩子）；
   - `--max-turns` 传递给 AIHandler 覆盖默认 MAX_TOOL_ROUNDS；
   - 结束后仿 run() 里 quit 的保存逻辑 `session_history.save_session(...)`（--no-save 跳过）。

## 四、非交互适配（关键难点，逐项落实）

1. **工具确认**：headless 下禁止任何终端确认交互。
   - 默认（yolo 模式）：`app.tool_executor.no_more_confirmations = True`（Web 端同款机制），零确认放行；
   - 显式 `--mode standard/auto` 时：确认请求一律按"拒绝"处理，工具结果中说明
     "非交互模式下该操作需要确认，请换 --mode yolo 或改用交互模式"，让 AI 自行改道或中止。
2. **交互类工具直接禁用**：headless 下 ask_user 等依赖终端交互的工具**从工具注册表禁用**，
   而不是执行时才返回提示（禁用后 `get_openai_tools()` 不返回该工具，LLM 看不到就不会调用，
   `execute()` 层同时拒绝，双保险，零轮次浪费）：
   - 实现用 `tool_registry` 的 disabled_tools 集合**增量添加** "ask_user" 等交互工具名
     （⚠️ 必须用"当前集合 ± 目标工具名"增量操作，禁止整体覆盖，避免误动 Agent 配置里
     其他被用户手动禁用的工具）；
   - 若激活了 Agent 链条，call_agent 调用的下游 Agent 触发 ask_user/确认时，
     同样按"用户不在线（非交互模式），请基于现有信息决策"自动返回，禁止阻塞等待输入。
3. **TTY 依赖组件全部关闭**：spinner 动画、thinking_display、input_box、Markdown 流式渲染
   一律不启用；最终回复以纯文本一次性打印（可保留 Markdown 源码格式）。
4. **ANSI 颜色码**：`sys.stdout.isatty()` 为 False 时全局禁用颜色/emoji 装饰，
   保证管道、重定向、CI 日志干净。
5. **无模型时的报错**：不能提示"使用 /model 命令配置"（headless 没有斜杠命令），
   改为 stderr 输出错误+解决指引（如何用交互模式或 Web 配置模型），退出码 1。
6. **KeyboardInterrupt**：捕获后输出中断信息，退出码 130，不打印交互式的"输入 quit 退出"。

## 五、输出与退出码

**text 模式**：stdout 只含最终回答（`--quiet` 连过程输出都没有，保证 `cbhcli exec -q "..." | jq` 可用）。

**json 模式**：结束后输出单条 JSON：

```json
{
  "result": "最终回答文本",
  "session_id": "会话ID（配合 --resume）",
  "agent": "实际使用的Agent",
  "model": "实际使用的模型",
  "rounds": 3,
  "usage": {"prompt_tokens": 1234, "completion_tokens": 567},
  "exit_reason": "done|max_turns|permission_denied|interrupted"
}
```

过程信息（工具调用等）一律走 stderr，stdout 只放结果（除非 --quiet 关闭时也走 stderr）。

**退出码**：

| 码 | 含义 |
|----|------|
| 0 | 成功 |
| 1 | 执行错误（模型未配置/API失败/Agent不存在） |
| 2 | 用法错误或权限拒绝 |
| 130 | 用户中断（Ctrl+C） |

## 六、约束

- 不修改 `run()` 交互主循环行为，`cbhcli`（无参启动）和 `cbhcli web` 必须零影响；
- 不动 Web/Jupyter 复用的核心路径（ai_handler/process_request 本体不改，只在外层包装）；
- 现有全局配置、Agent、权限规则文件全部只读使用（--mode 仅进程内生效，不持久化）；
- 不引入新依赖。

## 七、验收标准（逐条实测通过）

1. `cbhcli exec --mode yolo "列出当前目录的 py 文件并统计总行数"` -> 工具调用后输出结果并退出；
2. `git log --oneline -5 | cbhcli exec -q "总结这些提交"` -> stdout 仅含总结文本，无 ANSI 码；
3. `cbhcli exec --mode standard "删除 /tmp/test.txt"` -> 工具被拒，退出码 2；
4. `cbhcli exec -c "我们刚才聊了什么"` 在 1 之后执行 -> 能记住上一次会话内容；
5. `--output-format json` 输出可被 `jq .result` 解析；
6. `--no-save` 后交互式 `/history` 中不出现该会话；
7. 回归：`cbhcli` 交互模式、`cbhcli web`、`cbhcli --version` 行为不变（交互模式下 ask_user 仍可用）；
8. headless 全程 LLM 工具列表不含 ask_user（可从 history/traces 的 JSONL 或 --output-format json 验证），
   进程无任何时刻阻塞等待终端输入（`</dev/null` 重定向运行也不挂起）。

## 八、文档与版本同步

- cli.py `print_help()` 增加 exec 子命令用法块；
- README.md 命令表新增 `cbhcli exec` 行；
- 新建 `docs/vX.Y.Z更新日志.md`，按现有 7 处版本号位置流程升版本并打包安装验证。

---

## 附：设计决策备注（给执行 Agent 的上下文，不需要实现）

- **stdout/stderr 分离**是脚本集成的命门：所有过程输出走 stderr，stdout 只有结果，
  这样 `cbhcli exec -q ... | jq` 才可用。
- **会话默认保存**是为了 `--continue`/`--resume` 能续接，与 claude 的 `-c`/`--resume` 语义一致；
  `--no-save` 供一次性查询场景避免污染历史。
- **`--max-turns` 默认 999**：headless 无人监督场景下的轮次护栏，超限即停
  （exit_reason=max_turns）；长任务由用户显式调大。
- **默认 yolo（零确认）**：一次性入口的本意就是"放权让它干完"，省掉 --yes 类参数；
  需要收紧时显式 `--mode standard/auto`，此时确认类操作一律拒绝（退出码 2）。
- **JSONL 事件流**（codex 的 `--json` 逐事件流）为进阶需求，基础版跑通后可作为
  `--output-format jsonl` 追加，第一版不做。
- **工具确认机制**直接复用 Web 端已验证的 `tool_executor.no_more_confirmations` 方案，
  不新造轮子。
