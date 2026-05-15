# CBHCLI Agent与Session管理系统实现计划

## Context

CBHCLI当前是单一会话模式的AI终端助手,所有逻辑集中在`cbhcli_app.py`(1185行)的`TerminalAssistant`类中。用户需要扩展为多Agent管理系统,支持Agent工作空间、子Agent自动创建、会话管理、上下文压缩和多种文件操作工具,使其成为更强大的AI辅助开发环境。

## 实现方案

### 架构概览

将单文件重构为模块化包结构,核心组件:
- **AgentManager**: Agent CRUD与工作空间管理
- **Session + ContextWindow**: 增强会话管理与上下文追踪
- **ToolRegistry**: 统一工具注册与调用
- **ContextCompressor**: AI驱动的上下文压缩
- **VectorStore**: ChromaDB向量数据库集成
- **SlashCommandParser**: 斜杠命令系统

### 工作空间结构

```
agents/
└── {agent_name}/
    ├── config.json        # Agent配置(首选模型、上下文限制等)
    ├── skills.md          # Agent技能(自动加载到系统提示)
    ├── soul.md            # Agent性格(自动加载到系统提示)
    ├── tools.md           # 工具使用说明(自动加载到系统提示)
    └── memory.md          # 对话记录(不自动加载,通过memory_search查询)
```

### Phase 1: 基础设施重构

**1.1 创建模块化包结构**
```
cbhcli/
├── __init__.py
├── __main__.py              # 入口点
├── core/
│   ├── app.py               # CBHCLIApp主应用
│   ├── agent.py             # Agent, AgentConfig, AgentManager
│   ├── session.py           # Session, ContextWindow, Message
│   └── model.py             # ModelConfig, ModelManager
├── tools/
│   ├── registry.py          # ToolRegistry
│   ├── base.py              # BaseTool抽象基类
│   ├── terminal.py          # TerminalTool(重构现有)
│   ├── file_read.py         # ReadTool
│   ├── file_write.py        # WriteTool
│   ├── file_edit.py         # EditTool
│   └── memory_search.py     # MemorySearchTool
├── context/
│   ├── compressor.py        # ContextCompressor
│   └── token_counter.py     # Token计数器
├── vector/
│   ├── store.py             # VectorStore(ChromaDB)
│   └── indexer.py           # MemoryIndexer
├── commands/
│   ├── parser.py            # SlashCommandParser
│   ├── agent_cmd.py         # /agent命令
│   └── session_cmd.py       # /reset, /new, /comp命令
└── config/
    └── global_config.py     # 全局配置管理
```

**1.2 提取LLMClient统一API调用**
- 将散落在各处的`requests.post()`封装为`LLMClient`类
- 支持chat、chat_stream、embeddings方法
- 复用现有流式响应处理逻辑

**1.3 实现TokenCounter**
- 使用`tiktoken`库精确计数
- 降级方案: 字符数/4估算
- 为每条Message预计算token_count

**1.4 建立ToolRegistry + BaseTool框架**
```python
class BaseTool(ABC):
    @property
    def name(self) -> str
    @property
    def description(self) -> str
    @property
    def parameters(self) -> dict  # JSON Schema
    def execute(self, **kwargs) -> ToolResult
```

### Phase 2: Agent与Session核心

**2.1 AgentManager实现**
- `create_agent(name, description, model_name)` - 创建工作空间目录和MD文件
- `load_agent(name)` - 加载config.json
- `load_agent_persona(name)` - 读取skills.md + soul.md + tools.md
- `list_agents()` / `delete_agent(name)` / `switch_agent(name)`
- 工作空间路径: `agents/{agent_name}/`(项目目录下)

**2.2 Session与ContextWindow**
```python
@dataclass
class Message:
    role: str
    content: str
    token_count: int = 0
    timestamp: datetime

class Session:
    messages: list[Message]
    def add_message(role, content)
    def get_context_messages() -> list[dict]  # API格式
    def get_total_tokens() -> int
    def is_context_full(context_limit) -> bool
    def reset()  # 清空会话
```

**2.3 ContextWindow上下文管理**
- 追踪`current_usage / model_limit`百分比
- `needs_compression()` - 超过80%触发压缩
- 显示: `/ctx` 命令查看当前使用情况

**2.4 ContextCompressor上下文压缩**
- AI自动总结策略:
  1. 提取user/assistant消息
  2. 调用LLM生成摘要(专用compression prompt)
  3. 保留最早2-3轮 + 摘要 + 最近3-5轮
  4. 重新计算token总数
- 手动压缩: `/comp` 命令触发

### Phase 3: 工具实现

**3.1 重构TerminalTool**
- 从现有`execute_command()`提取
- 保持命令确认机制
- 支持多命令执行(&&连接)

**3.2 ReadTool**
```json
{"tool": "read", "arguments": {"file_path": "/path/to/file.py"}}
```
- 读取文件内容,支持大文件分页
- 显示行号和文件路径

**3.3 WriteTool**
```json
{"tool": "write", "arguments": {"file_path": "/path/to/file.py", "content": "..."}}
```
- 创建或覆盖文件
- 执行前需用户确认(危险操作)

**3.4 EditTool**
```json
{"tool": "edit", "arguments": {"file_path": "...", "old_str": "...", "new_str": "..."}}
```
- 精确字符串替换
- 验证`old_str`唯一匹配
- 不匹配时返回错误和可能的位置

**3.5 MemorySearchTool**
```json
{"tool": "memory_search", "arguments": {"query": "...", "top_k": 5}}
```
- 使用ChromaDB语义搜索
- 索引memory.md中的重要信息
- Embedding策略: 优先OpenAI兼容API,降级本地sentence-transformers

### Phase 4: 命令系统

**4.1 SlashCommandParser**
```python
class SlashCommand:
    name: str
    description: str
    usage: str
    handler: Callable
    requires_agent: bool
```

**4.2 实现命令**
| 命令 | 描述 | 需要Agent |
|------|------|-----------|
| `/agent create <name>` | 创建新Agent | 否 |
| `/agent list` | 列出所有Agent | 否 |
| `/agent switch <name>` | 切换当前Agent | 否 |
| `/agent delete <name>` | 删除Agent | 否 |
| `/reset` | 清空当前会话 | 是 |
| `/new` | 创建新会话 | 是 |
| `/comp` | 手动压缩上下文 | 是 |
| `/ctx` | 显示上下文使用情况 | 是 |
| `/help` | 显示帮助 | 否 |

**4.3 新会话初始化流程**
1. 用户输入`/new`或`/reset`
2. 清空session.messages
3. 读取当前Agent的skills.md, soul.md, tools.md
4. 构建System Prompt:
   ```
   [Agent skills]
   [Agent soul/personality]
   [Agent tools usage guide]
   [Available tools from ToolRegistry]
   ```
5. 开始新会话(无历史会话记忆)

### Phase 5: 子Agent机制

**5.1 SubAgent临时实例**
```python
class SubAgent(Agent):
    parent: Agent
    task: str
    session: Session  # 独立会话
    status: PENDING/RUNNING/COMPLETED/FAILED
```

**5.2 子Agent生命周期**
1. 父Agent判断任务复杂度(LLM评估)
2. `SubAgentScheduler.spawn(parent, task)` 创建临时子Agent
3. 继承父Agent的model、persona(skills/soul/tools)
4. 独立Session和ToolCallingAgent循环
5. 子Agent只能使用terminal/read/write/edit工具(不能用memory_search)
6. 完成后返回结果摘要给父Agent
7. 销毁SubAgent实例,释放内存

**5.3 父-子通信**
- 结果注入: `"SubAgent [{name}] 结果: {summary}"`
- 同步执行(完成后才能创建下一个)

### Phase 6: 向量数据库集成

**6.1 VectorStore封装ChromaDB**
```python
class VectorStore:
    def add_documents(agent_name, texts, ids, metadata)
    def query(agent_name, query_text, top_k) -> list[Document]
```

**6.2 MemoryIndexer索引策略**
- 按段落分割memory.md(`\n\n`分隔符)
- 每次memory.md更新后重新索引
- 持久化到`~/.cbhcli/agents/{agent_name}/.chroma/`

**6.3 ChromaDB作为可选依赖**
- 未安装时memory_search提示用户安装
- 降级为简单文本搜索

### Phase 7: 集成与配置

**7.1 重构主应用CBHCLIApp**
- 整合所有组件
- 重构`run()`主循环:
  ```
  用户输入 → 斜杠命令? → 执行handler
                  ↓ 否
           ToolCallingAgent循环
  ```

**7.2 更新配置文件格式**
```json
{
  "models": [...],
  "agents": {
    "default_agent": "general",
    "active_agent": "dev-helper"
  },
  "settings": {
    "auto_compress": true,
    "compression_ratio": 0.8
  }
}
```

**7.3 更新依赖**
```
# pyproject.toml新增
chromadb>=0.4.0          # 可选
tiktoken>=0.5.0
```

**7.4 向后兼容**
- 自动迁移旧配置格式
- 无Agent时创建默认`general` Agent
- 保持现有工具调用JSON格式不变

## 关键文件

- **cbhcli_app.py** - 重构源文件,需拆分为多个模块
- **pyproject.toml** - 添加入口点和新增依赖
- **__init__.py** - 更新包结构

## 验证方案

1. **单元测试**: 每个工具类独立测试
2. **集成测试**: Agent创建→会话→工具调用→上下文压缩完整流程
3. **手动测试**:
   ```bash
   # 1. 创建Agent
   /agent create dev-helper
   
   # 2. 测试文件工具
   /agent create test-agent
   请使用write工具创建文件test.py,内容为print("hello")
   请使用read工具读取test.py
   请使用edit工具将test.py中的hello改为world
   
   # 3. 测试会话管理
   /ctx                    # 查看上下文使用
   /comp                   # 手动压缩
   /new                    # 新会话
   
   # 4. 测试memory_search
   请搜索之前关于文件操作的对话
   ```
4. **子Agent测试**: 复杂任务自动创建子Agent并返回结果
