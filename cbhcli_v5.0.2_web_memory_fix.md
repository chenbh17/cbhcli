# CBHCLI v5.0.2 修改详情 — Web 端长期记忆（memory.md）未注入系统提示修复

> 用途：将本次修改同步到其他机器的 cbhcli 源码。
> 修改日期：2025-07
> 影响范围：仅 Web 端（CLI 端不受影响，CLI 原本就正常）

---

## 一、问题描述

Web 界面中，AI 的系统提示词**缺少长期记忆（memory.md）内容**。
CLI 端正常，Web 端从未注入 memory.md，导致 AI 在 Web 界面"记不住"用户要求记住的信息。

## 二、根因分析

Web 端 `WebChatSession._rebuild_system_prompt()`（`cbhcli_pkg/web/server.py`）
调用 `persona.build_system_prompt()` 时**漏传 `memory_content` 参数**。

虽然 `AgentManager.load_agent_persona()` 会读取 memory.md 到 `persona.memory` 属性，
但 `AgentPersona.build_system_prompt()`（`cbhcli_pkg/core/agent.py`）只使用
**`memory_content` 参数**，并不使用 `self.memory` 属性——
所以 `persona.memory` 被加载后从未被使用，长期记忆"静默丢失"。

CLI 端（`cbhcli_pkg/core/app.py` 的 `_reset_session` / `_update_system_prompt`）
通过 `self._load_memory_md()` 读取 memory.md 并显式传入，所以 CLI 正常。

对比：

```python
# CLI app.py（正常）——读取 memory.md 并传入
memory_content = self._load_memory_md()
system_prompt = self.current_persona.build_system_prompt(
    agent_name=...,
    model_name=...,
    memory_content=memory_content,      # ← CLI 传了这个参数
    active_skills_prompt=...,
    cwd=os.getcwd(),
    supports_vision=supports_vision,
)

# Web server.py（修复前，bug）——没传 memory_content
system_prompt = persona.build_system_prompt(
    agent_name=self.agent_name,
    model_name=self.model_name,
    active_skills_prompt=active_skills_prompt,
    cwd=os.getcwd(),
    supports_vision=getattr(self.llm_client, "supports_vision", False),
)                                       # ← 缺 memory_content
```

## 三、修复内容（唯一修改文件：cbhcli_pkg/web/server.py）

### 修改 1：`WebChatSession` 类新增 `_load_memory_md()` 方法

复刻 CLI `app.py` 同名方法的逻辑：读取 memory.md，跳过 `---` 之前的模板说明部分。

在 `WebChatSession` 类中、`_rebuild_system_prompt` 方法**之前**插入：

```python
    def _load_memory_md(self) -> str:
        """读取 memory.md 文件内容（复刻 CLI app.py _load_memory_md 逻辑）。

        跳过模板说明部分（--- 之前），只保留实际记忆内容。
        """
        if not self.agent_config:
            return ""
        memory_file = self.agent_config.workspace_path / "memory.md"
        if memory_file.exists():
            try:
                content = memory_file.read_text(encoding='utf-8')
                lines = content.split('\n')
                in_content = False
                content_lines = []
                for line in lines:
                    if line.strip() == '---':
                        in_content = True
                        continue
                    if in_content:
                        content_lines.append(line)
                return '\n'.join(content_lines).strip()
            except Exception:
                pass
        return ""
```

### 修改 2：`_rebuild_system_prompt()` 中读取并传入 `memory_content`

在 `persona.build_system_prompt(...)` 调用**之前**加一行读取，
并在参数中加上 `memory_content=memory_content,`：

```python
        # 读取长期记忆（与 CLI 一致，memory.md 始终包含在系统提示中）
        memory_content = self._load_memory_md()
        system_prompt = persona.build_system_prompt(
            agent_name=self.agent_name,
            model_name=self.model_name,
            memory_content=memory_content,              # ← 新增这一行
            active_skills_prompt=active_skills_prompt,
            cwd=os.getcwd(),
            supports_vision=getattr(self.llm_client, "supports_vision", False),
        )
```

## 四、版本号同步更新（6 个文件 7 处：5.0.1 → 5.0.2）

| # | 文件 | 修改内容 |
|---|------|----------|
| 1 | `cbhcli_pkg/__init__.py` 第2行 | 注释 `v5.0.1` → `v5.0.2` |
| 2 | `cbhcli_pkg/__init__.py` 第6行 | `__version__ = "5.0.2"` |
| 3 | `pyproject.toml` 第7行 | `version = "5.0.2"` |
| 4 | `cbhcli_pkg/core/mcp_client.py` 第202行 | `"version": "5.0.2"` |
| 5 | `cbhcli_pkg/web/server.py` 第75行 | `FastAPI(..., version="5.0.2")` |
| 6 | `README.md` 第1行 | `# CBHCLI v5.0.2 - AI驱动的终端助手` |
| 7 | `README.md` 安装命令行 | `pip install dist/cbhcli-5.0.2-py3-none-any.whl` |

## 五、同步方式（二选一）

### 方式 A：直接打 patch（推荐，最快）

将本目录下的 `cbhcli_v5.0.2_web_memory_fix.patch` 复制到目标机器的项目根目录，执行：

```bash
cd <目标机器cbhcli项目根目录>
patch -p1 < cbhcli_v5.0.2_web_memory_fix.patch
# 或者如果项目用 git 管理：
git apply cbhcli_v5.0.2_web_memory_fix.patch
```

### 方式 B：手动按上文"三、修复内容"修改

按第三节的两处代码修改 + 第四节的版本号更新，手动编辑对应文件。

## 六、打包安装（修改后执行）

```bash
cd <项目根目录>
rm -rf dist/* build *.egg-info
python -m build
pip install dist/cbhcli-5.0.2-py3-none-any.whl --force-reinstall --no-deps
```

## 七、验证

```bash
cbhcli --version
# 应输出: cbhcli version 5.0.2

grep -c "_load_memory_md" <安装目录>/cbhcli_pkg/web/server.py
# 应输出: 3（方法定义1处 + docstring提及0处 + 调用1处 + 注释1处，共3处匹配）

grep -A1 "memory_content = self._load_memory_md" <安装目录>/cbhcli_pkg/web/server.py
# 应能看到 memory_content 被传入 build_system_prompt
```

功能验证：启动 Web 界面（`cbhcli web`），开新会话后问 AI
"你的长期记忆中有什么内容"，AI 应能复述 memory.md 中的内容。

## 八、影响评估

- **仅 Web 端受益**：CLI 端原本就正常，本次修改不影响 CLI 任何行为
- **零破坏性**：只新增一个方法 + 一个参数，不改任何已有逻辑
- **生效时机**：Web 会话创建、权限模式切换、Agent 文件更新、切换模型时
  都会触发 `_rebuild_system_prompt()`，长期记忆即自动注入
