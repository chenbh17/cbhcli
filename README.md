# CBHCLI v4.5 - AI驱动的终端助手

一个功能强大的AI驱动终端助手，支持多Agent管理、工具调用、知识库和会话管理。

## 特性

### 核心功能
- **多Agent管理** - 创建和管理多个AI助手Agent，每个Agent有独立的工作空间和人格配置
- **工具调用系统** - 基于 OpenAI Function Calling 协议，AI自动调用工具执行任务
- **MCP协议支持** - 连接外部工具服务器，扩展AI的工具能力
- **知识库系统** - 为每个Agent建立专属知识库，支持语义搜索和问答
- **会话管理** - 上下文窗口监控、自动压缩、会话重置
- **向量检索** - 基于ChromaDB的语义搜索，支持历史对话和知识库检索

### 14大内置工具
| 工具 | 功能 |
|------|------|
| `terminal` | 执行终端命令 |
| `read` | 读取文件内容 |
| `write` | 创建/覆盖文件 |
| `edit` | 精确字符串替换 |
| `grep` | 正则搜索文件内容 |
| `glob` | 按模式匹配搜索文件 |
| `ask_user` | 向用户提问获取决策 |
| `Todo` | 任务计划列表管理 |
| `python` | 执行Python代码（带会话记忆） |
| `memory_search` | 语义搜索向量化知识内容 |
| `knowledge_base` | 查询知识库内容 |
| `skills_create` | 创建新技能 |
| `delegate_task` | 将子任务委托给子Agent执行 |

### MCP 扩展工具
通过 MCP (Model Context Protocol) 协议连接外部工具服务器，无限扩展AI的能力。
添加的MCP工具与内置工具使用方式完全相同。

### 高级功能
- **多步规划** - 复杂任务自动拆解为 Todo 计划列表，逐步执行并追踪进度
- **自我反思** - 工具执行失败时自动分析原因并重试（最多3次）
- **子Agent协作** - 将独立子任务委托给子Agent执行，拥有独立上下文
- **嵌入模型支持** - 可配置专用嵌入模型API（OpenAI compatible）
- **重排序服务** - 支持Jina、Cohere等重排序API提高检索质量
- **自动上下文压缩** - 当接近模型限制时自动压缩上下文
- **多模型支持** - 配置多个OpenAI兼容的AI模型，随时切换

## 安装

### 前置要求
- Python 3.8 或更高版本
- pip 包管理器

### 从源码安装
```bash
# 克隆或下载此仓库
cd cbhcli

# 安装（开发模式）
pip install -e .

# 或标准安装
pip install .
```

### 从Wheel安装
```bash
pip install dist/cbhcli-4.5.0-py3-none-any.whl
```

### 可选依赖
```bash
# 向量数据库支持（用于语义搜索）
pip install chromadb

# 精确Token计数
pip install tiktoken
```

## 快速开始

```bash
# 启动应用
cbhcli

# 查看帮助
/help

# 查看版本
cbhcli --version
```

## 使用指南

### 1. 配置模型
```
/model add
# 按提示输入: 模型名称、API Key、Base URL、模型ID、上下文长度

/model list    # 查看已配置的模型
/model use     # 交互式选择模型
```

### 2. 创建Agent
```
/agent add dev-helper
# 按提示输入: Agent描述、选择模型
```

### 3. 使用工具
AI会通过 Function Calling 自动调用工具完成任务，例如：
- "帮我创建一个test.py文件，内容为print('hello')"
- "读取当前目录下的所有文件"
- "搜索我之前关于数据库配置的讨论"

无需手动输入任何调用格式，AI通过 Function Calling 协议自动调用工具。

### 4. 知识库管理
```
/kb add /path/to/document.pdf     # 添加文件到知识库
/kb list                          # 列出知识库文件
/kb reindex                       # 重新索引
/kb status                        # 查看状态
```

### 5. 配置嵌入模型和向量索引
```
/model embedding add     # 配置嵌入模型（用于向量搜索）
# 按提示输入: 模型名称、API Key、Base URL、模型ID、模型类型

/embedding index         # 手动触发索引（启动时不会自动索引）
/embedding status        # 查看索引状态
/embedding reindex       # 重新索引
```

### 6. 会话管理
```
/new 或 /reset    # 创建新会话（自动保存当前会话到history文件夹）
/resume           # 列出历史会话
/resume 1         # 恢复第1个历史会话
/history          # 查看历史会话列表
/comp             # 手动压缩上下文
/ctx              # 查看上下文使用情况
```

### 7. memory.md 长期记忆
memory.md 用于保存用户要求记住的重要信息：
- **不会自动写入**：只有用户明确要求"记住"、"记录"时才写入
- **始终包含在系统提示中**：每次对话都会读取 memory.md 内容
- 普通对话历史通过 `/history` 和 `/resume` 管理

### 8. Python 工具
使用 `python` 工具执行 Python 代码，支持会话记忆：
- **会话记忆**：同一会话中定义的变量和导入的模块会保留
- 示例：第一次导入 pandas 并读取数据，第二次可以直接使用之前的变量
- **清空时机**：使用 `/reset` 或 `/new` 创建新会话时清空
- 适用于数据探索、计算、转换等任务

#### PyInstaller 打包环境下使用三方包

使用 PyInstaller 打包的可执行文件运行时，`python` 工具会自动探测服务器上的系统 Python 环境，将其 `site-packages` 路径注入到搜索路径中，使用户代码可以 `import` 系统已安装的三方包（如 pandas、numpy 等）。

**自动探测优先级**：
1. 环境变量 `CBHCLI_PYTHON`
2. `PATH` 中的 `python3`
3. `PATH` 中的 `python`
4. `/usr/bin/python3`、`/usr/local/bin/python3`

**指定 Python 环境**：如果服务器上有多个 Python 环境，可通过环境变量指定使用哪一个：

```bash
# 指定 conda 环境
export CBHCLI_PYTHON=/home/user/miniconda3/bin/python

# 指定 virtualenv
export CBHCLI_PYTHON=/home/user/myenv/bin/python

# 指定系统 Python
export CBHCLI_PYTHON=/usr/bin/python3
```

可将此配置写入 `~/.bashrc` 或 `~/.bash_profile` 使其永久生效：

```bash
echo 'export CBHCLI_PYTHON=/path/to/your/python' >> ~/.bashrc
source ~/.bashrc
```

## 配置

### 全局配置
配置文件位于 `~/.cbhcli/config.json`：

```json
{
  "models": [
    {
      "name": "my-gpt4",
      "apiKey": "sk-xxx",
      "url": "https://api.openai.com/v1",
      "model": "gpt-4o",
      "context_limit": 128000
    }
  ],
  "embedding_model": {
    "name": "openai-embedding",
    "apiKey": "sk-xxx",
    "url": "https://api.openai.com/v1",
    "model": "text-embedding-3-small",
    "type": "openai"
  },
  "rerank_model": {
    "name": "jina-reranker",
    "apiKey": "jina_xxx",
    "url": "https://api.jina.ai/v1",
    "model": "jina-reranker-v2-base-multilingual",
    "top_n": 5
  },
  "agents": {
    "default_agent": "main",
    "active_agent": "dev-helper"
  },
  "settings": {
    "auto_compress": true,
    "compression_ratio": 0.8,
    "workspace_base": "~/.cbhcli/agents"
  }
}
```

### Agent工作空间
每个Agent的工作空间位于 `~/.cbhcli/agents/<agent_name>/`：

```
agent_name/
├── config.json      # Agent配置
├── soul.md          # 性格设定
├── tools.md         # 工具使用指南
├── memory.md        # 长期记忆（用户指定内容）
├── usage.md         # 使用说明
├── history/         # 会话历史（自动保存）
├── knowledge/       # 知识库目录
│   └── *.md, *.txt, *.py, ...
└── skills/          # 技能目录
    └── <技能名>/
        ├── skills.md
        └── script/
```

## 向量索引工作流程

### 为什么需要手动索引？
- 启动时自动索引会消耗大量 API 调用和时间
- 文件内容不常变化，无需每次启动都重新索引
- 手动触发可以在需要时（如文件更新后）才执行

### 完整流程
1. **配置嵌入模型**: `/model embedding add`
2. **首次索引**: `/embedding index`
3. **文件更新后**: `/embedding reindex`
4. **查看状态**: `/embedding status`

### 索引范围
- soul.md - 性格特征
- tools.md - 工具指南
- usage.md - 使用说明
- knowledge/ - 知识库目录下所有文件

**注意**：memory.md 不索引到向量数据库，它始终作为长期记忆包含在系统提示中。
对话历史保存到 history/ 文件夹，通过 /resume 命令恢复。

## MCP 工具服务器

### 什么是 MCP？
MCP (Model Context Protocol) 是一个开放协议，允许 AI 通过 HTTP 调用远程服务器上的工具。
通过 MCP，你可以无限扩展 AI 的工具能力，连接任何外部服务。

### 添加 MCP 服务器
```bash
/mcp add myserver http://localhost:8080/mcp
# 带认证：
/mcp add authed http://localhost:8080/mcp Authorization=Bearer xxx
```

### 管理 MCP 服务器
```bash
/mcp list                   # 列出所有服务器
/mcp tools myserver         # 查看服务器的工具列表
/mcp refresh myserver       # 刷新工具列表
/mcp off srv tool           # 禁用指定工具
/mcp on srv tool            # 启用指定工具
/mcp rm myserver            # 移除服务器
```

### 使用 MCP 工具
添加的 MCP 工具会自动注册，AI 通过 Function Calling 自动调用。

## 命令参考

### 斜杠命令

| 命令 | 功能 |
|------|------|
| `/agent add <name>` | 创建新Agent |
| `/agent list` | 列出所有Agent |
| `/agent use [name]` | 切换Agent |
| `/agent rm [name]` | 删除Agent |
| `/model add` | 添加新模型 |
| `/model list` | 列出所有模型 |
| `/model use [name]` | 使用指定模型 |
| `/model rm [name]` | 删除模型 |
| `/model info` | 查看当前模型 |
| `/reset` 或 `/new` | 创建新会话（自动保存当前会话） |
| `/resume [编号]` | 列出或恢复历史会话 |
| `/history` | 查看历史会话列表 |
| `/comp` | 压缩上下文 |
| `/ctx` | 查看上下文使用 |
| `/kb add <file>` | 添加文件到知识库 |
| `/kb list` | 列出知识库文件 |
| `/kb rm [file]` | 删除知识文件 |
| `/kb reindex` | 重新索引知识库 |
| `/kb status` | 查看知识库状态 |
| `/embedding index` | 索引 Agent 工作空间到向量数据库 |
| `/embedding status` | 查看索引状态 |
| `/embedding clear` | 清除向量索引 |
| `/embedding reindex` | 重新索引（清除后重建） |
| `/mcp add <名> <URL>` | 添加 MCP 服务器 |
| `/mcp list` | 列出 MCP 服务器 |
| `/mcp tools [名]` | 查看服务器工具 |
| `/mcp rm [名]` | 移除 MCP 服务器 |
| `/mcp refresh [名]` | 刷新服务器工具 |
| `/mcp on [服务器] [工具]` | 启用工具 |
| `/mcp off [服务器] [工具]` | 禁用工具 |
| `/skills list` | 列出已注册技能 |
| `/skills add [name]` | 创建技能 |
| `/skills use [name]` | 激活技能 |
| `/skills off [name]` | 取消激活技能 |
| `/skills rm [name]` | 删除技能 |
| `/help [command]` | 显示帮助 |
| `quit` | 退出程序 |

### 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+R` | 切换工具显示详细/简洁模式 |

## 项目结构

```
cbhcli_pkg/
├── core/              # 核心模块
│   ├── app.py         # 主应用
│   ├── agent.py       # Agent管理
│   ├── session.py     # 会话管理
│   ├── session_history.py  # 会话历史管理
│   ├── model.py       # LLM客户端
│   ├── ai_handler.py  # AI请求处理（Function Calling + 规划/反思）
│   ├── tool_executor.py # 工具执行
│   ├── subagent.py    # 子Agent调度器
│   ├── response_cleaner.py # 响应清理
│   ├── embedding_client.py # 嵌入模型客户端
│   ├── rerank_client.py    # 重排序客户端
│   └── knowledge_base.py   # 知识库管理
├── tools/             # 工具实现
│   ├── terminal.py
│   ├── file_read.py
│   ├── file_write.py
│   ├── file_edit.py
│   ├── grep.py           # 正则搜索
│   ├── glob_tool.py      # 文件模式搜索
│   ├── ask_user.py       # 用户提问交互
│   ├── todo.py           # 任务计划列表
│   ├── python_tool.py    # Python执行（带会话记忆）
│   ├── memory_search.py
│   ├── knowledge_base.py
│   ├── delegate_task.py  # 子Agent任务委托
│   └── skills_create.py
├── config/            # 配置管理
├── context/           # 上下文管理
├── vector/            # 向量数据库
└── commands/          # 斜杠命令
```

## 开发

```bash
# 创建虚拟环境
python -m venv venv && source venv/bin/activate

# 安装开发依赖
pip install -e ".[dev]"

# 构建
python -m build

# PyInstaller 打包
pyinstaller cbhcli.spec --noconfirm
```

## 卸载

```bash
pip uninstall cbhcli
```

## License

MIT
