# cbhcli 用户 Agent 调用链条功能开发需求

> **重要前置说明**：本需求中的「Agent 链条」是指**用户创建的持久化 Agent**（通过 `/agent add` 创建，拥有独立工作空间、系统提示、工具配置、长期记忆、技能、MCP 等）之间的通信与编排机制。这与现有的 `delegate_task` / `subagent` 子 Agent 工具**完全无关**——子 Agent 工具是临时性的、无持久身份的并行任务执行器。链条中的每个用户 Agent 仍然保留各自完整的能力（包括各自独立使用 `delegate_task` 做临时子 Agent 并行），链条功能是在用户 Agent 之间新增一层通信编排能力。

---

## 一、功能概述

为 cbhcli 新增**用户 Agent 调用链条（Agent Chain）**功能。用户可以定义一条由多个用户 Agent 组成的有序调用链，链条顶端的元 Agent 是用户直接对话的入口。元 Agent 在处理用户请求时，可以按链条拓扑**调用下游用户 Agent**——即加载下游 Agent 的完整配置（系统提示、工具、工作空间、记忆、技能等），以该 Agent 的身份执行任务，完成后将结果回传给上游 Agent。由此实现多 Agent 协作，用户只需与元 Agent 对话即可完成跨多个 Agent 的复杂工作流。

### 典型场景

现有 Agent：`main`、`cbhcli`、`dify-chat`、`dbm-vl`

配置链条 `dev-deploy`：
```
Level 1 (元 Agent):     main
                           │
Level 2:                cbhcli
                        ╱        ╲
Level 3:        dify-chat      dbm-vl
```

用户对 `main` 说："修改 cbhcli 的文件内容，重新打包安装，然后用 dify-chat 推送到 GitHub，用 dbm-vl 推送到远程服务器。"

执行流程：
1. `main`（元 Agent）接收请求，根据系统提示中注入的下游 Agent 描述和链条调用说明，理解任务需要调用链条下游 Agent
2. `main` 调用 `cbhcli`（Level 2），以 `cbhcli` 的完整身份执行文件修改 + 打包安装
3. `cbhcli` 完成后结果回传 `main`
4. `main` 同时调用 `dify-chat` 和 `dbm-vl`（Level 3，同级可并行），分别以各自完整身份执行 GitHub 推送和远程服务器推送
5. 两个 Agent 完成后结果回传 `main`
6. `main` 向用户汇总全部结果

**关键**：上述过程中 `cbhcli`、`dify-chat`、`dbm-vl` 各自使用自己的系统提示、工具配置、工作空间、长期记忆、技能等——它们是真正的用户 Agent，不是临时子 Agent。

---

## 二、核心概念定义

### 2.1 Agent 链条（Agent Chain）

一条有序的 Agent 调用路径，由多个层级（Level）组成，每个层级可包含一个或多个同级用户 Agent。

| 规则 | 说明 |
|------|------|
| **根 Agent（元 Agent）** | 链条最顶层（Level 1）的 Agent，是用户直接对话的入口。元 Agent 负责理解用户意图、编排下游调用、汇总结果 |
| **上下游通信** | 只有相邻层级的上下游 Agent 之间可以通信。上游 Agent 向下游 Agent 发送任务消息，下游 Agent 执行完毕后向上游 Agent 回传结果 |
| **同级隔离** | 同一层级的 Agent 之间**不能**相互通信，各自独立执行上游分派的任务，互不感知 |
| **单向流转** | 链条通信是自上而下发起、自下而上回传。下游 Agent **不能**主动调用上游 Agent，也不能调用同级 Agent |
| **单链绑定** | 每个对话会话（Session）只能绑定一条 Agent 链条。未绑定链条时行为与现有单 Agent 完全一致 |
| **权限继承** | 链条内所有 Agent 的权限模式与元 Agent（根 Agent）一致，由当前会话权限模式统一控制。下游 Agent 被调用时继承当前权限模式，不单独设置 |
| **身份独立** | 链条中的每个 Agent 被调用时，加载该 Agent 自己的完整配置（AgentConfig + AgentPersona：系统提示、工具开关、工作空间、memory.md、技能、MCP 服务器等），以自己的身份和能力执行任务 |

### 2.2 下游 Agent 描述与链条调用说明

上游 Agent 需要知道何时触发哪个下游 Agent，信息来源有两层：

**第一层：Agent 描述（自动读取）**
- 链条绑定后，系统自动将各下游 Agent 的 `description` 字段（即 `/agent add` 时填写的描述）注入到上游 Agent 的系统提示中
- 例如 `cbhcli` 的 description 为 "cbhcli 代码更新助手，负责查看和更新 cbhcli 代码"，上游 Agent 据此判断代码相关任务应调用 `cbhcli`
- 此字段已存在于 AgentConfig 中，无需额外配置

**第二层：链条调用说明（用户自定义）**
- 用户在配置链条时，可为每个上下游连接关系编写**调用说明**（call_instruction），补充描述上游 Agent 在什么情况下应该调用该下游 Agent
- 这是可选字段，不填则仅依靠 Agent 的 description 判断
- 调用说明比 description 更具体、更具指导性，例如："当用户要求修改 cbhcli 代码、重新打包安装时，调用 cbhcli Agent 执行。cbhcli 完成后，如果需要推送代码到 GitHub，调用 dify-chat；如果需要推送到远程服务器，调用 dbm-vl。"

**系统提示注入示例**（元 Agent main 的系统提示中追加）：
```
## Agent 链条信息
当前已激活链条: dev-deploy
你的角色: 元 Agent (Level 1)

你可以通过 call_agent 工具调用以下下游 Agent:

Level 2:
  - cbhcli: cbhcli 代码更新助手，负责查看和更新 cbhcli 代码
    调用说明: 当用户要求修改 cbhcli 代码、重新打包安装时调用

Level 3 (cbhcli 的下游):
  - dify-chat: Dify 聊天助手，负责 GitHub 代码推送
    调用说明: 当 cbhcli 完成代码修改后需要推送到 GitHub 时调用
  - dbm-vl: 数据库管理助手，负责远程服务器部署
    调用说明: 当 cbhcli 完成代码修改后需要推送到远程服务器时调用
```

### 2.3 链条调用机制（与子 Agent 工具的区别）

| 维度 | Agent 链条调用 | delegate_task 子 Agent 工具 |
|------|---------------|---------------------------|
| **Agent 身份** | 用户创建的持久化 Agent，有独立工作空间/系统提示/工具/记忆/技能/MCP | 临时创建的匿名 Agent，无持久身份，共享当前 Agent 的工作空间 |
| **配置加载** | 完整加载目标 Agent 的 AgentConfig + AgentPersona（soul.md / tools.md / usage.md / memory.md）+ 工具开关 + 技能 + MCP | 使用当前 Agent 的配置，仅传入任务描述 |
| **通信方向** | 链条拓扑：上游 → 下游（单向），结果回传 | 任意并行，无拓扑约束 |
| **使用场景** | 跨 Agent 工作流编排（如：开发 → 部署 → 推送） | 当前 Agent 内部并行拆分子任务 |
| **共存关系** | 链条中的每个 Agent 仍可各自独立使用 delegate_task 做临时子 Agent 并行 | — |

### 2.4 下游 Agent 调用流程

当元 Agent（或上游 Agent）需要调用下游 Agent 时：

1. **加载目标 Agent 完整配置**：通过 AgentManager 加载目标 Agent 的 `AgentConfig`（工具开关、模型配置等）和 `AgentPersona`（soul.md / tools.md / usage.md / memory.md）
2. **构建独立会话上下文**：为下游 Agent 创建独立的 Session（不影响上游 Agent 的会话），构建该 Agent 的系统提示（包含其性格、工具指南、使用说明、长期记忆、技能等）
3. **注入链条上下文**：在下游 Agent 的系统提示中注入链条角色信息——告知它当前处于哪条链条的哪个层级、上游 Agent 是谁、它的任务定位是什么
4. **发送任务消息**：上游 Agent 将任务描述作为 user 消息发送给下游 Agent
5. **下游 Agent 独立执行**：下游 Agent 以自己的完整身份和能力处理任务（可使用自己的工具、技能、MCP 服务器，也可使用 delegate_task 做临时子任务并行）
6. **结果回传**：下游 Agent 执行完毕后，将其最终回复作为结果回传给上游 Agent
7. **上游 Agent 继续**：上游 Agent 收到结果后，决定是否继续调用下一层 Agent 或向用户汇总

---

## 三、CLI 端功能需求

### 3.1 新增 `/chain` 斜杠命令

新增 `/chain` 命令管理 Agent 链条，包含以下子命令：

| 子命令 | 格式 | 功能说明 |
|--------|------|----------|
| `list` | `/chain list` | 列出所有已配置的 Agent 链条，以树形结构展示每个链条的层级关系 |
| `add` | `/chain add <链条名>` | 交互式创建新链条：输入链条名称 → 逐层选择已有 Agent → 为每个连接填写调用说明（可选）→ 确认保存 |
| `rm` | `/chain rm <链条名>` | 删除指定链条配置（不影响链条中引用的 Agent 本身） |
| `use` | `/chain use <链条名>` | 在当前会话中激活指定链条（若当前已绑定链条，提示确认是否切换） |
| `off` | `/chain off` | 取消当前会话的链条绑定，恢复为普通单 Agent 模式 |
| `config` | `/chain config <链条名>` | 编辑指定链条的层级结构和调用说明（增删层级节点、替换 Agent、调整层级、修改调用说明） |
| `show` | `/chain show <链条名>` | 查看指定链条的详细配置：树形展示 + 各层 Agent 名称/描述/模型 + 调用说明 |
| `rename` | `/chain rename <旧名> <新名>` | 重命名链条 |

### 3.2 交互式创建流程（`/chain add`）

执行 `/chain add dev-deploy` 后进入交互式创建：

```
🔗 创建 Agent 链条: dev-deploy

请输入链条描述 (可选): 开发部署链条

── Level 1 (元 Agent) ──
选择 Agent: [main / cbhcli / dify-chat / dbm-vl] → main

── Level 2 ──
选择 Agent (可多选，逗号分隔): → cbhcli
调用说明 (可选，描述何时调用 cbhcli): → 当用户要求修改cbhcli代码、重新打包安装时调用
继续添加下一层? (y/n): → y

── Level 3 ──
选择 Agent (可多选，逗号分隔): → dify-chat, dbm-vl
调用说明 - dify-chat (可选): → 当代码修改完成后需要推送到GitHub时调用
调用说明 - dbm-vl (可选): → 当代码修改完成后需要推送到远程服务器时调用
继续添加下一层? (y/n): → n

确认链条结构:
  main
    └── cbhcli  [当用户要求修改cbhcli代码、重新打包安装时调用]
          ├── dify-chat  [当代码修改完成后需要推送到GitHub时调用]
          └── dbm-vl  [当代码修改完成后需要推送到远程服务器时调用]

保存? (y/n): → y
✅ 链条 dev-deploy 已保存
```

### 3.3 链条列表展示（`/chain list`）

```
🔗 Agent 链条列表

[1] dev-deploy — 开发部署链条
    main
      └── cbhcli  [修改cbhcli代码、重新打包安装]
            ├── dify-chat  [推送到GitHub]
            └── dbm-vl  [推送到远程服务器]

[2] data-pipeline — 数据处理链条
    main
      └── dbm-vl  [数据库操作]

当前会话: 🔗 dev-deploy (已激活)
```

### 3.4 链条详情展示（`/chain show`）

```
🔗 链条详情: dev-deploy

描述: 开发部署链条

main (Level 1 · 元 Agent)
  模型: glm-5.2
  │
  └── cbhcli (Level 2)
        描述: cbhcli 代码更新助手，负责查看和更新 cbhcli 代码
        模型: glm-5.2
        调用说明: 当用户要求修改cbhcli代码、重新打包安装时调用
        │
        ├── dify-chat (Level 3)
        │     描述: Dify 聊天助手，负责 GitHub 代码推送
        │     模型: glm-5.2
        │     调用说明: 当代码修改完成后需要推送到GitHub时调用
        │
        └── dbm-vl (Level 3)
              描述: 数据库管理助手，负责远程服务器部署
              模型: glm-5.2
              调用说明: 当代码修改完成后需要推送到远程服务器时调用
```

### 3.5 链条配置存储

链条配置持久化存储在 `~/.cbhcli/agent_chains.json`（独立文件，不侵入现有 config.json）：

```json
{
  "chains": {
    "dev-deploy": {
      "name": "dev-deploy",
      "description": "开发部署链条",
      "levels": [
        {
          "level": 1,
          "agents": ["main"]
        },
        {
          "level": 2,
          "agents": [
            {
              "name": "cbhcli",
              "call_instruction": "当用户要求修改cbhcli代码、重新打包安装时调用"
            }
          ]
        },
        {
          "level": 3,
          "agents": [
            {
              "name": "dify-chat",
              "call_instruction": "当代码修改完成后需要推送到GitHub时调用"
            },
            {
              "name": "dbm-vl",
              "call_instruction": "当代码修改完成后需要推送到远程服务器时调用"
            }
          ]
        }
      ],
      "created_at": "2025-01-15T10:30:00"
    }
  }
}
```

> **说明**：`call_instruction` 存储在 `agents` 数组的每个对象中。Level 1（元 Agent）不需要 `call_instruction`（它不被任何上游调用）。Agent 的 `description` 不存储在链条配置中——系统在注入系统提示时实时从 AgentConfig 读取，保证 Agent 描述更新后链条自动同步。

### 3.6 会话内链条激活与状态栏显示

- **未绑定链条**：状态栏显示当前 Agent 名称（与现有行为一致），如 `main`
- **绑定链条后**：状态栏显示链条标识 + 元 Agent 名称，如 `🔗 dev-deploy › main`
- **调用下游 Agent 时**：状态栏实时显示当前正在执行的 Agent 路径，如 `🔗 dev-deploy › main › cbhcli`
- **下游 Agent 执行完毕回传后**：状态栏恢复为 `🔗 dev-deploy › main`

### 3.7 元 Agent 调用下游 Agent 的实现机制

元 Agent 如何调用下游用户 Agent——这是本功能的核心实现点：

1. **系统提示注入链条信息**：当会话绑定链条后，元 Agent 的系统提示中注入链条结构描述，包含：
   - 链条名称和描述
   - 每个下游 Agent 的名称 + description（实时从 AgentConfig 读取）+ call_instruction（用户自定义的调用说明）
   - 层级关系树形展示
   - `call_agent` 工具的使用说明

2. **新增 Agent 通信工具**：新增一个内置工具 `call_agent`，供链条中的上游 Agent 调用下游 Agent：
   - 参数：`agent_name`（目标 Agent 名称）、`task`（任务描述/消息内容）
   - 工具执行逻辑：
     - 校验 `agent_name` 是否为当前链条中该 Agent 的合法下游
     - 通过 AgentManager 加载目标 Agent 的完整配置（AgentConfig + AgentPersona）
     - 为目标 Agent 创建独立的临时 Session（不干扰上游 Agent 的会话）
     - 构建目标 Agent 的系统提示（含该 Agent 的 soul.md / tools.md / usage.md / memory.md / 技能 / 链条角色信息）
     - 初始化目标 Agent 的工具注册表（按该 Agent 的 disabled_tools 配置过滤）、MCP 管理器、技能管理器
     - 构建目标 Agent 的 LLM 客户端（使用该 Agent 的 primary_model 或继承当前模型）
     - 将 `task` 作为 user 消息发送给目标 Agent，执行 ReAct 循环
     - 目标 Agent 完成后，返回最终回复文本
   - 返回结果：目标 Agent 的最终回复作为工具结果回传给上游 Agent

3. **同级并行调用**：元 Agent 可在同一次回复中多次调用 `call_agent`（同层多个下游 Agent），系统并行执行，全部完成后结果分别回传

4. **权限继承**：下游 Agent 执行期间继承当前会话的权限模式（PermissionEngine 全局单例），不单独设置

5. **工具隔离**：下游 Agent 的工具执行使用自己的工具注册表（包含该 Agent 配置的工具开关、MCP 服务器、技能），与上游 Agent 的工具互不干扰

6. **工作空间隔离**：下游 Agent 使用自己的工作空间路径（各自的 `~/.cbhcli/agents/<name>/`），文件操作、检查点、Hooks 等均基于自己的工作空间

### 3.8 链条中 Agent 的会话管理

- 下游 Agent 被调用时创建的临时 Session **不持久化**到下游 Agent 的 history（避免污染下游 Agent 的独立会话历史）
- 下游 Agent 的执行过程（工具调用、思考内容）以折叠/缩进方式展示给用户，标明来自哪个 Agent
- 下游 Agent 执行期间的 tracer 记录写入上游 Agent（元 Agent）的 trace 文件，标注被调用的 Agent 名称

---

## 四、Web 端功能需求

### 4.1 链条配置管理界面

在 Web 端新增 Agent 链条配置管理功能（可在现有「Agents」视图中新增 Tab 页或独立视图入口）：

**左侧 - 链条列表**：
- 展示所有已配置链条名称和描述
- 点击选中查看详情
- 底部「+ 新建链条」按钮

**右侧 - 链条详情/编辑**：
- 以**树形/缩进卡片**展示链条层级结构，每个节点显示：
  - Agent 名称
  - Agent 描述（从 AgentConfig 实时读取）
  - 模型名称
  - 调用说明（call_instruction，如有）
- 层级之间用连线/缩进明确表示上下游关系
- 同层多个 Agent 横向并列展示
- 支持编辑：增删层级节点、替换 Agent（下拉选择已有 Agent）、调整层级顺序、编辑调用说明
- 支持删除链条（确认弹窗）

**新建链条表单**：
- 输入链条名称和描述
- 逐层添加：每层从已有 Agent 下拉多选
- 每个连接可填写调用说明（call_instruction 文本框，可选）
- 实时预览链条树形结构（含调用说明）
- 确认保存

### 4.2 聊天界面 - Agent 链条指示器

在 Web 聊天界面**右上角**新增 Agent 链条指示器组件：

- **未绑定链条**：显示当前 Agent 名称（如 `main`），点击可弹出链条选择菜单
- **绑定链条后**：显示链条名 + 当前层级路径
  - 空闲状态：`🔗 dev-deploy › main`
  - 调用下游时：`🔗 dev-deploy › main › cbhcli`（实时更新）
  - 多个同级并行时：`🔗 dev-deploy › main › cbhcli + dify-chat + dbm-vl`
- **鼠标悬停**：展开完整链条树形结构（tooltip 或 popover），含各 Agent 描述和调用说明
- **点击**：弹出菜单可快速切换链条或取消绑定（`off`）

### 4.3 聊天界面 - 下游 Agent 调用过程展示

当元 Agent 调用下游 Agent 时，聊天界面需要清晰展示调用过程：

- 发出一条系统消息标识调用开始：`📌 调用 Agent: cbhcli (Level 2)`
- 下游 Agent 的执行过程（思考内容、工具调用、回复）以**可折叠区块**展示，带 Agent 名称标签和颜色区分
- 同级并行调用时，多个 Agent 的执行区块并列展示
- 调用完成后：`✅ cbhcli 完成` + 结果摘要

### 4.4 会话级链条绑定 API

新增 Web API：

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/api/chains` | 获取所有链条配置 |
| POST | `/api/chains` | 创建新链条 |
| PUT | `/api/chains/{name}` | 更新链条配置（含调用说明编辑） |
| DELETE | `/api/chains/{name}` | 删除链条 |
| GET | `/api/chains/{name}` | 获取单个链条详情 |
| POST | `/api/chat/use-chain` | 当前会话绑定链条（参数：chain_name） |
| POST | `/api/chat/off-chain` | 取消当前会话链条绑定 |

SSE 新增事件：

| 事件 | 触发时机 | 数据 |
|------|---------|------|
| `chain_call_start` | 上游 Agent 调用下游 Agent | `{agent_name, level, task}` |
| `chain_call_content` | 下游 Agent 流式输出 | `{agent_name, content}` |
| `chain_call_tool` | 下游 Agent 工具调用 | `{agent_name, tool_name, ...}` |
| `chain_call_end` | 下游 Agent 执行完成 | `{agent_name, result}` |
| `chain_status` | 链条状态变更 | `{chain_name, active_path}` |

### 4.5 Web 端会话级链条状态

- Web 端新建会话时，可在会话创建参数中指定 `chain_name` 绑定链条
- 已有会话可通过 API 动态绑定/解绑链条
- 链条绑定状态保存在 WebChatSession 中
- 元 Agent 的系统提示中注入链条信息（与 CLI 一致，含下游 Agent 描述 + 调用说明）
- 下游 Agent 调用通过 `call_agent` 工具触发，Web 端通过 SSE 事件流式展示

---

## 五、存量兼容性需求

| 场景 | 处理方式 |
|------|----------|
| 现有用户 Agent | 默认无链条绑定，行为完全不变 |
| 现有会话 | 不受影响，用户可随时通过 `/chain use` 或 Web 界面添加链条 |
| 未配置任何链条 | `/chain list` 显示空列表并提示创建；CLI/Web 所有功能正常不受影响 |
| 链条中引用的 Agent 被删除 | 链条配置标记为「无效」，提示用户修复（替换或删除该节点）或删除链条 |
| Agent 被多个链条引用 | 允许，同一个 Agent 可出现在不同链条的不同层级中 |
| Agent 描述更新 | 链条系统提示自动同步最新描述（每次注入时实时读取 AgentConfig） |
| 未绑定链条时 `call_agent` 工具 | 不注册该工具（或注册但调用时返回"当前未绑定 Agent 链条"提示） |
| 绑定链条后 `/agent use` 切换 Agent | 若切换的 Agent 不是当前链条的元 Agent，提示"切换将取消链条绑定"，确认后取消链条 |

---

## 六、涉及的代码模块

请根据 cbhcli 现有架构评估并修改以下模块：

### 6.1 新增文件

| 文件 | 说明 |
|------|------|
| `cbhcli_pkg/core/agent_chain.py` | **链条核心模块**：AgentChain 数据结构（含 levels + call_instruction）、ChainManager 管理器（CRUD + 持久化到 agent_chains.json）、链条校验逻辑、系统提示注入方法（读取下游 Agent description + call_instruction 生成链条上下文文本）、下游 Agent 调用执行器（加载目标 Agent 完整配置 → 构建独立会话 → 执行 ReAct → 回传结果） |
| `cbhcli_pkg/commands/chain_cmd.py` | **`/chain` 命令实现**：注册命令及子命令 handler，交互式创建/编辑流程（含调用说明填写） |
| `cbhcli_pkg/tools/call_agent.py` | **`call_agent` 工具**：供链条中上游 Agent 调用下游 Agent 的内置工具，封装调用逻辑 |

### 6.2 修改文件

| 文件 | 改动内容 |
|------|----------|
| `cbhcli_pkg/core/agent.py` | AgentConfig 无需改动（description 已存在）；AgentManager 可能需要提供加载 Agent 工具注册表的方法供链条调用复用；`CBHCLI_USAGE_GUIDE` 添加 `/chain` 命令说明；`TOOLS_TEMPLATE` 添加 `call_agent` 工具使用说明（标注仅链条绑定时可用） |
| `cbhcli_pkg/core/app.py` | 会话级链条绑定状态（`self.current_chain`）；`_load_agent` 后注入链条信息到系统提示；状态栏显示链条状态；注册 `call_agent` 工具（仅绑定链条时注册）；`/chain` 命令注册；斜杠命令补全添加 `/chain` |
| `cbhcli_pkg/core/input_box.py` | 斜杠命令补全菜单添加 `/chain` 及其子命令 |
| `cbhcli_pkg/core/ai_handler.py` | ReAct 循环中处理 `call_agent` 工具调用时，流式展示下游 Agent 的执行过程（带 Agent 名称标签） |
| `cbhcli_pkg/core/permissions.py` | 确认下游 Agent 工具执行时权限引擎正确传递（PermissionEngine 全局单例已满足） |
| `cbhcli_pkg/core/tool_executor.py` | 下游 Agent 工具执行时正确挂载该 Agent 的 HookManager / CheckpointManager / Tracer（基于下游 Agent 的工作空间） |
| `cbhcli_pkg/cli.py` | `print_help()` 中添加 `/chain` 命令说明 |
| `cbhcli_pkg/config/global_config.py` | 链条配置的全局管理接口（或由 ChainManager 独立管理 agent_chains.json） |
| `cbhcli_pkg/web/server.py` | 链条配置 CRUD API；会话级链条绑定 API；WebChatSession 扩展链条状态；系统提示注入链条信息（含下游 Agent description + call_instruction）；注册 `call_agent` 工具；SSE 新增链条调用事件 |
| `cbhcli_pkg/web/static/js/app.js` | 链条配置管理视图（树形展示含调用说明 + 表单编辑）；聊天界面右上角链条指示器（悬停展示完整链条含描述和调用说明）；下游 Agent 调用过程折叠区块展示；链条切换菜单 |
| `cbhcli_pkg/web/static/css/style.css` | 链条树形展示样式、指示器样式、Agent 调用区块样式（不同 Agent 颜色区分）、调用说明文本样式 |
| `cbhcli_pkg/web/static/index.html` | 新增链条管理视图入口；静态资源缓存参数更新 |
| `cbhcli_pkg/commands/tools_cmd.py` | `BUILTIN_TOOLS` 列表添加 `call_agent`；`_write_tools_md()` 同步更新 |
| `README.md` | `/chain` 命令文档 + Agent 链条功能介绍 |
| `docs/` | 链条功能设计文档 |

### 6.3 现有 Agent 工作空间更新

创建链条功能后，需要更新所有现有 Agent 的 `usage.md` 和 `tools.md`（通过批量脚本）：
- usage.md：添加 `/chain` 命令说明
- tools.md：添加 `call_agent` 工具说明（标注仅链条绑定时可用）

---

## 七、`call_agent` 工具设计

### 工具定义

```
工具名: call_agent
功能: 调用 Agent 链条中的下游 Agent 执行任务
参数:
  - agent_name (string, required): 要调用的下游 Agent 名称（必须是当前链条中你的合法下游）
  - task (string, required): 交给下游 Agent 的任务描述
返回: 下游 Agent 的执行结果文本
```

### 工具执行流程

```
call_agent(agent_name="cbhcli", task="修改 xxx 文件并重新打包安装")
  │
  ├─ 1. 校验: cbhcli 是否为当前链条中当前 Agent 的合法下游
  │     └─ 否 → 返回错误 "cbhcli 不是当前 Agent 的下游 Agent"
  │
  ├─ 2. 加载 cbhcli 完整配置
  │     ├─ AgentConfig (config.json): 工具开关、模型、上下文限制等
  │     ├─ AgentPersona (soul.md / tools.md / usage.md / memory.md)
  │     ├─ SkillManager: cbhcli 的技能目录
  │     └─ MCPManager: cbhcli 的 MCP 服务器配置
  │
  ├─ 3. 构建独立会话
  │     ├─ 新建 Session (agent_name="cbhcli")
  │     ├─ 构建系统提示 (cbhcli 的 persona + 链条角色信息)
  │     ├─ 构建工具注册表 (按 cbhcli 的 disabled_tools 过滤)
  │     ├─ 初始化 LLMClient (cbhcli 的 primary_model 或继承当前模型)
  │     └─ 挂载 Harness 组件 (cbhcli 工作空间的 HookManager/Checkpoint/Tracer)
  │
  ├─ 4. 执行 ReAct 循环
  │     ├─ 将 task 作为 user 消息发送
  │     ├─ cbhcli 以自己的完整身份处理 (可用自己的工具/技能/MCP)
  │     ├─ cbhcli 也可使用 delegate_task 做临时子 Agent 并行 (独立于链条)
  │     └─ 流式输出通过事件回调实时传回 (CLI: 终端展示 / Web: SSE 推送)
  │
  └─ 5. 返回结果
        └─ cbhcli 的最终回复文本作为工具结果回传给上游 Agent
```

### 关键设计要点

1. **工具注册条件**：`call_agent` 工具仅在会话绑定了 Agent 链条时注册到工具列表中。未绑定链条时该工具不存在，元 Agent 的系统提示中也不包含链条信息
2. **下游 Agent 的工具注册表**：独立于上游 Agent，按下游 Agent 的 `disabled_tools` 配置过滤。下游 Agent 同时也可以有 `call_agent` 工具（如果链条中它还有更下游的 Agent）
3. **下游 Agent 的模型**：优先使用下游 Agent 的 `primary_model`，若未配置则继承当前会话的模型
4. **权限传递**：下游 Agent 的 ToolExecutor 使用全局 PermissionEngine 单例（与上游 Agent 共享权限模式），但 HookManager / CheckpointManager / Tracer 使用下游 Agent 自己工作空间的实例
5. **流式输出回调**：下游 Agent 执行期间，其思考内容、工具调用、回复文本通过回调函数实时传回上层，CLI 端在终端以缩进/折叠方式展示，Web 端通过 SSE 事件推送

---

## 八、验收标准

### CLI 端

1. ✅ `/chain add dev-deploy` 可交互式创建包含 `main → cbhcli → {dify-chat, dbm-vl}` 的链条，可为每个连接填写调用说明
2. ✅ `/chain list` 以树形结构展示所有链条（含调用说明摘要）
3. ✅ `/chain show dev-deploy` 展示完整链条详情（含 Agent 描述 + 调用说明）
4. ✅ `/chain use dev-deploy` 后，状态栏显示 `🔗 dev-deploy › main`，元 Agent 系统提示中包含下游 Agent 的 description + call_instruction
5. ✅ 对 `main` 说"修改 cbhcli 文件并重新打包安装，然后推送到 GitHub 和远程服务器"，`main` 根据链条调用说明自动调用 `cbhcli`（以 cbhcli 完整身份执行），再并行调用 `dify-chat` 和 `dbm-vl`（各自以完整身份执行），最终 `main` 汇总结果
6. ✅ 下游 Agent 执行过程中，终端清晰展示来自哪个 Agent 的输出
7. ✅ `/chain off` 取消链条绑定后，`main` 恢复为普通单 Agent，`call_agent` 工具不再可用
8. ✅ 同级 Agent（如 dify-chat 和 dbm-vl）之间无法相互调用
9. ✅ 链条中的每个 Agent 使用自己的工具配置、工作空间、记忆、技能
10. ✅ 修改某个 Agent 的 description 后（`/agent config`），下次会话激活链条时系统提示自动同步最新描述

### Web 端

11. ✅ Web 配置界面可可视化管理链条（树形展示含调用说明、新建/编辑/删除）
12. ✅ 聊天界面右上角显示当前链条状态，鼠标悬停展示完整链条树（含描述和调用说明），可点击切换/取消
13. ✅ 下游 Agent 调用过程以折叠区块展示，带 Agent 名称标签
14. ✅ 链条绑定/解绑通过 API 操作，SSE 事件正确推送

### 兼容性

15. ✅ 存量 Agent 和会话不受影响，无链条时一切如常
16. ✅ 链条中引用的 Agent 被删除时，链条标记为无效并提示修复
17. ✅ 同一 Agent 可被多个链条引用

---

## 九、注意事项

1. **链条功能为可选增强**：不影响未使用链条的用户体验，未绑定链条时 `call_agent` 工具不注册
2. **与 delegate_task 完全独立**：链条调用的是用户 Agent（有完整持久身份），delegate_task 是临时子 Agent（无持久身份）。两者可共存——链条中的 Agent 仍可使用 delegate_task 做临时并行
3. **权限统一**：链条内所有 Agent 共享当前会话的权限模式，不单独设置
4. **链条配置变更不影响进行中的会话**：下次会话生效或用户主动重新绑定
5. **下游 Agent 会话不持久化**：避免污染下游 Agent 的独立会话历史
6. **Agent 描述实时读取**：call_instruction 存储在链条配置中，但 Agent 的 description 每次从 AgentConfig 实时读取，保证描述更新后链条自动同步
7. **版本号按实际改动递增**：同步更新所有版本号位置（7 个文件 8 处 + index.html 3 处缓存参数）
8. **打包安装**：改完后执行 `python -m build` + `pip install dist/cbhcli-<版本>-py3-none-any.whl --force-reinstall --no-deps`
