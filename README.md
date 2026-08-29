# CBHCLI v5.3.0 - AI驱动的终端助手

一个功能强大的AI驱动终端助手，支持多Agent管理、工具调用、知识库和会话管理。

## 特性

### 核心功能
- **多Agent管理** - 创建和管理多个AI助手Agent，每个Agent有独立的工作空间和人格配置
- **工具调用系统** - 基于 OpenAI Function Calling 协议，AI自动调用工具执行任务
- **MCP协议支持** - 连接外部工具服务器，扩展AI的工具能力
- **知识库系统** - 为每个Agent建立专属知识库，支持语义搜索和问答
- **会话管理** - 上下文窗口监控、自动压缩、会话重置
- **向量检索** - 基于ChromaDB的语义搜索，支持历史对话和知识库检索
- **Web界面（v4.9.4 全面重构，v5.2.9 实时架构升级）** - 对标 CLI 全部功能：WebSocket 实时流式对话（多浏览器同会话画面一致/会话后台运行不中断/侧边栏运行状态徽标）+ 工作区会话管理（按文件夹分组/三点菜单重命名复制删除）+ 文件管理器双面板 + 11 个管理视图（Agent/模型/备用模型/技能/MCP/知识库/工具/索引/历史/设置），原生 JS 零构建依赖，现代深色主题
- **技能系统** - 可复用的提示词+脚本技能，按需激活增强AI能力
- **Markdown渲染** - CLI界面支持Markdown格式渲染，代码高亮、表格、列表等美观显示
- **LaTeX公式渲染** - 支持LaTeX数学公式渲染，行内公式与块级公式均可正常显示
- **ReAct持续交付** - ReAct循环中自动压缩上下文，突破上下文窗口限制实现长任务持续交付
- **聊天输入框** - 基于 prompt_toolkit 原生补全系统，字素簇宽度感知，支持中英文/emoji（含ZWJ/VS16/旗帜/肤色）输入退格无错位、resize防抖防重叠、斜杠命令补全菜单
- **统一交互输入** - 所有交互式提问（/model add 等）统一使用 prompt_toolkit 输入系统，中英文/emoji 退格行为一致
- **状态栏** - 输入框下方状态栏以文字标签+高对比配色显示：模型/上下文/Agent/技能/完整路径
- **并行子Agent** - AI智能拆分多个独立子任务并行委托（最多100个并发），rich.Live 实时状态板展示每个子Agent当前步骤，全部完成后主Agent再继续
- **工具预览语法高亮** - edit/write/python 工具预览统一用 rich.Table + Pygments 语法高亮（monokai 主题）渲染：edit 差异对比宽屏左右并排、窄屏上下堆叠，write/python 按文件类型自动分色（30+种扩展名识别），长行自动折行，行列精确对齐
- **后台任务管理** - terminal 命令超时(默认30秒)不杀进程转后台运行，process 工具实时监控进度直到完成，任务满1小时自动终止，kill_process 工具随时手动终止，长任务（pip install 大包等）不再被误杀
- **图片识别双模式（v4.9.5）** - 识图统一走 image 工具：主模型支持视觉时图片作为多模态消息直发主模型（共享会话上下文，零额外API调用）；主模型无视觉时自动调用其他已配置视觉模型识别（fallback链），识别能力不被主模型限制
- **会话内热切换模型（v4.9.6）** - `/model use` 切换模型不再重建会话，当前对话内容完整保留，仅替换LLM客户端并原地更新系统提示；视觉主模型 fallback 到非视觉模型时自动将历史带图消息降级为纯文本，不再报"不是视觉模型"错误
- **状态栏即时显示（v4.9.7）** - 修复输入框下方状态栏（模型/上下文/Agent/技能/路径）偶尔不显示的问题：AI 回答期间按键产生 type-ahead 残留时，prompt_toolkit 跳过 CPR 光标查询导致高度永远未知、工具栏被过滤器隐藏，现改为首轮渲染即显示
- **权限模式（v4.9.9 Harness 治理层）** - 四档权限模板 Shift+Tab 循环热切换：🔒readonly 只读（AI 只能看）/ 🟢standard 标准（危险操作逐个确认，默认）/ 🟡auto 自动（工作区内写操作自动放行）/ 🔴yolo 最高权限（零确认直接执行，deny 红线降级警告）；内置 deny 红线规则（rm -rf /、写 .env/.git 等物理禁止）+ ask 危险操作（git push/sudo/rm 等）+ allow 只读命令；确认框新增 always 选项自动提炼永久放行规则；/mode /permissions 命令管理
- **Hooks 钩子系统（v4.9.9）** - 生命周期 6 事件（SessionStart/UserPromptSubmit/PreToolUse/PostToolUse/SubagentStop/Stop）自动执行自定义 shell 命令：PreToolUse 可拦截危险操作（退出码2+stderr 反馈模型）、PostToolUse 可自动跑测试/格式化并反馈、Stop 可自动通知/保存；配置于 ~/.cbhcli/hooks.json 或 Agent 工作空间，/hooks 命令管理
- **死循环熔断（v4.9.9）** - 自动检测两类死循环：工具同参数重复调用（3次警告附加结果、4次熔断并告知模型换策略、3次干预仍无效则终止任务）与 A→B→A→B 周期震荡；流式输出文本复读（150字符块出现3次）自动截断；全程不中断任务，只有彻底没救才告知用户
- **文件检查点回滚（v4.9.9）** - write/edit 执行前自动备份目标文件（Agent 工作空间 backups/，保留最近50份），/undo 一键回滚最近一次修改或按 ID 回滚指定备份
- **调用链追踪（v4.9.9 可观测性）** - 每次工具调用（名称/参数摘要/权限判定/耗时/成败）、模式切换、循环熔断、压缩等事件自动落盘 JSONL（工作空间 history/traces/），可追溯可审计
- **工具调用显示升级（v4.9.9）** - Claude Code 风格：⏺ 工具头 + braille 旋转动画实时显示执行耗时 + ✓/✗ 状态行 + ⎿ 树形结果引导线（超出12行折叠提示 Ctrl+R 查看全部）
- **死循环检测修复（v5.0.0）** - 周期震荡检测从工具名序列改为签名序列（工具名+参数MD5），修复 read/edit 等不同参数交替调用被误判为循环的问题；Web 端子 Agent 并行上限从 10 提升到 100（与 CLI 对齐）
- **Web AI 发送文件/图片（v5.0.0）** - Web 界面 AI 可主动向用户发送文件和图片：新增 `send_file` 工具（仅 Web 端注册，CLI 不可见）传入文件路径即可发送，图片内联显示点击放大，文件显示下载链接；python 生成图片（matplotlib 等）自动检测展示，write/edit 写文件自动生成下载链接；所有逻辑仅在 Web 端生效，CLI 零改动
- **Agent 链条（v5.1.0）** - 用户 Agent 间调用编排：定义多层级 Agent 调用链（如 main → cbhcli → {dify-chat, dbm-vl}），元 Agent 通过 `call_agent` 工具按链条拓扑调用下游用户 Agent（各自以完整身份执行：系统提示/工具/工作空间/记忆/技能/MCP），结果回传汇总；同级可并行；`/chain` 命令管理（list/add/rm/use/off/show/config/rename）；Web 端链条管理视图 + 聊天界面链条指示器 + 下游调用折叠展示
- **edit 工具 Unicode 转义宽容匹配与错误诊断（v5.1.5）** - edit 工具对 Unicode 转义序列（\uXXXX 等）做宽容匹配，old_str 与实际文件内容在转义形式不同时仍可正确命中替换；匹配失败时输出精确诊断信息（未找到片段定位、候选相似片段提示），大幅降低跨编码/转义场景下的误配与排查成本
- **Web 端 reasoning_effort 下拉框含 max 选项（v5.1.5）** - Web 模型配置界面 reasoning_effort 下拉框新增 `max` 选项（与 CLI 对齐），支持更深度的推理强度配置
- **上下文压缩四项优化（v5.1.6）** - ① 压缩目标可控：修复 target_tokens 死参数，压缩后降到窗口 30%（摘要 max_tokens 预算限制 + 超目标迭代降级保留轮数）；② 摘要提示词对标 Claude Code：CRITICAL 约束 + analysis/summary 双块 + 9 章节结构化模板；③ 压缩可撤销：自动备份到 history/compressions/，`/undo-compress` 一键恢复压缩前原始消息；④ 摘要输入保留工具调用链（`[工具 terminal 结果]`）+ 大输出截断
- **文本复读检测误报修复（v5.1.7）** - TextLoopDetector 改为双块整体匹配（尾部 2×150 字符）+ 近邻窗口（最近 5000 字符）统计，且重复 ≥15 次才判定复读（连续重复总长 <4500 字符永不触发）。修复正常思考中"先设计后实现"重复写出的模板/骨架片段（如 HTML 头部）被误判死循环、思考被截断的问题；真正的连续大量复读仍会正常熔断。改 loop_detector.py 1 个文件，CLI/Web 调用方零改动
- **上下文压缩失败修复（v5.1.8）** - 修复 `/comp` 压缩"假装成功"的严重 bug：① 摘要 max_tokens 封顶到 64k（`SUMMARY_MAX_TOKENS=65536`），消除大窗口模型（context_limit≥44万）下预算超过 API max_tokens 上限（131072）导致的 400 错误；② 摘要生成失败不再返回 `[压缩失败...]` 占位文本塞进会话，改为抛异常 + `compress()` 捕获后保持会话原样、记录 `last_error`、返回 False，杜绝失败污染上下文；③ CLI/Web 各调用点失败时展示具体原因（补可观测性）。改 6 个文件
- **一次性非交互执行（v5.2.5）** - `cbhcli exec` 对标 `claude -p`：shell/CI/管道一次性下发任务，fd 级 stdout/stderr 分离，进程内 monkey-patch 零改核心文件，退出码 0/1/2/130 + JSON 输出
- **每轮对话自动保存（v5.2.6）** - CLI/Web 全部出口（含中断/SSE 断开/死循环熔断）自动落盘会话，服务重启最多丢当前轮
- **Web 侧边栏工作区会话管理（v5.2.8）** - 会话按工作空间文件夹分组可折叠，打开任意文件夹作工作空间（chdir+系统提示重建）；会话/文件夹三点菜单（重命名/复制/删除/选择/新增/清空）；文件管理器面板与工作区面板左右互换+拖拽调宽；CLI `/resume` 扩容 50 条+关键词搜索
- **Web 实时架构升级（v5.2.9）** - ① **WebSocket 多浏览器同步**：聊天从 SSE 改为 `/ws` 事件总线，事件按单调 seq 记录、迟到订阅按 since_seq 回放，多个浏览器打开同一会话画面完全一致（含流式思考/工具确认实时同步，多浏览器确认首个应答生效）；② **会话后台运行**：新建/切换会话不中断正在执行的任务（后台 asyncio 任务继续跑），侧边栏实时显示"●运行中"徽标；会话身份以 session.id 为准，同 agent:model 可并存多会话；③ **工具运行中可中断**：terminal 等工具执行期间点中断立即杀子进程组（实测 sleep 30 中断 0.6s 结束）；④ 设置页返回导航（设置主页←返回会话、详情页←设置）；⑤ CLI/Web 共享会话文件的内存副本新鲜度校验（磁盘被外部更新自动重载）
- **QQ Bot 集成（v5.3.0 增强）** - 基于官方 WebSocket/REST API v2：① 网关鉴权头修复（Bearer→`QQBot {token}`，修复长期运行后"获取网关地址失败"无限重连）；② `qqbot_send_message` 工具支持主动发送：所有与 Bot 交互过的用户 openid 持久化到 `~/.cbhcli/qqbot_registry.json`（跨进程共享），AI 可直接指定 openid 主动发消息（无需用户先发消息）、按昵称模糊查找 openid（`find_user`）、列出全部已知用户（`action=list`）、指定 Bot（`bot_name`）；发送走 REST API 不依赖网关在线；QQ 官方频控错误码自动翻译为可操作建议；Agent 链条下游 Agent 同样可用

### 17大内置工具 + Web 专属工具 + QQ Bot 工具
| 工具 | 功能 |
|------|------|
| `terminal` | 执行终端命令（超时自动转后台，不杀进程） |
| `process` | 实时监控后台任务进度，等待完成获取全部输出 |
| `kill_process` | 终止运行时间过长的后台任务 |
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
| `delegate_task` | 委托子任务给子Agent（单个串行 / 多个并行最多100个，实时状态板） |
| `call_agent` | 调用链条中下游用户Agent执行任务（仅链条绑定时可用） |
| `image` | 识别图片内容（主模型支持视觉时直发主模型，否则调用其他视觉模型） |
| `qqbot_send_message` | 通过 QQ Bot 发送消息/文件：可直接指定 openid 主动发送（无需用户先发消息），支持按昵称查找 openid（find_user）、列出已知用户（action=list）、指定 Bot（bot_name），openid 持久化注册表跨进程共享 |
| `send_file` 📡 | **仅 Web 端**：向用户发送文件/图片，图片内联显示、文件下载链接 |

### cbhpacks 数据科学工具（13个，默认关闭）
基于 [cbhpacks](https://github.com/chenbh17/cbhpacks) 数据科学工具包封装，覆盖完整的机器学习建模流水线。默认不开启，通过 `/tools on` 手动开启。

**会话级状态缓存（v4.9.3）**：与 python 工具共享会话命名空间 —— 各工具的执行结果变量（如 `bm`/`woe_data`/`iv_data`/`mt`/`clf`/`selected_cols`/`data`）自动注入会话，可在 python 工具中直接使用做二次分析；同参数重复调用自动复用缓存实例（如 fit 后的模型可直接调参/出报告）。`/new` 或 `/reset` 后所有缓存与变量自动释放。

| 工具 | 功能 | 方法 |
|------|------|------|
| `cbhpacks_bins_model` | 分箱WOE/IV/PSI计算 | comp_woe_iv, bins_rpt, data_to_woe, get_psi, psi_mth_avg, plot_col_rpt, plot_cols_rpt |
| `cbhpacks_binary_model` | 二分类模型训练评估 | lr_fit, xgb_fit, lgbm_fit, mlp_fit, svm_fit, rdf_fit, para_adj_gs, para_adj_bs, report |
| `cbhpacks_uns_model` | 无监督学习PCA/聚类 | pca, get_keams_cluster, kmeans |
| `cbhpacks_linear_model` | 线性回归/工具变量 | ols, IV |
| `cbhpacks_cols_select` | 特征筛选(10种方法) | null_select, enumerate_select, iv_select, psi_select, corr_select, chi2_select, logistic_select, ml_select, boostrap_select, vif_select |
| `cbhpacks_cols_select_js` | 递归特征筛选 | recursion_select |
| `cbhpacks_cols_encode` | 特征编码(7种方法) | data_to_sigmoid, data_to_sc, data_to_minmax, data_to_softmax, bins_to_num, str_to_num, data_to_woe |
| `cbhpacks_cols_operate` | 列操作(炸裂/转置/分词) | col_explode, col_to_T, col_to_cols, date_col_trans, date_mth_year, jieba_trans |
| `cbhpacks_desc_df` | 数据集描述统计 | get_rpt |
| `cbhpacks_desc_col` | 单变量分析/异常值检测 | desc_, relative_, supervised_, easy_od, feat_card |
| `cbhpacks_con_sql` | 数据库连接SQL执行 | chrun, chdf, con_mysql, con_hive, get_create_table, to_hive, rfms_sql |
| `cbhpacks_con_linux` | Linux SSH连接命令 | con_linux, data_trans_linux, jps, hadoop, start_hive |
| `cbhpacks_get_random_data` | 生成随机测试数据 | (直接生成) |

**典型建模流水线**：
```
cbhpacks_get_random_data → cbhpacks_desc_df → cbhpacks_cols_encode → cbhpacks_bins_model
→ cbhpacks_cols_select → cbhpacks_binary_model → report
```

### MCP 扩展工具
通过 MCP (Model Context Protocol) 协议连接外部工具服务器，无限扩展AI的能力。
添加的MCP工具与内置工具使用方式完全相同。

### Web 界面（v4.9.4 全面重构，v5.2.9 实时架构升级）
基于 FastAPI + 原生 JS SPA（零构建依赖，marked 内置离线可用），对标 CLI 全部功能与逻辑：
- **实时对话（WebSocket）** - 流式渲染 Markdown、思考块折叠、工具调用卡片（默认收起，失败/含图自动展开）、工具确认条（允许/拒绝/全部允许/始终允许该命令）、ask_user 交互问答、自我反思重试提示、工具运行中随时中断；**多个浏览器打开同一会话画面完全一致**，多浏览器同时弹出确认时首个应答生效
- **会话后台运行** - 新建/切换会话不中断正在执行的任务，后台继续运行并在侧边栏显示"●运行中"徽标；每轮自动落盘，刷新页面自动恢复到上次会话
- **工作区会话管理** - 左侧栏按工作空间文件夹分组展示会话（可折叠），会话/文件夹三点菜单（重命名/复制/删除/选择该文件夹/新增会话/删除全部）；打开任意文件夹作为工作空间，Agent 随之感知新工作目录
- **文件管理器** - 双面板（工作区⇄文件管理器左右互换、拖拽调宽），文件下载/复制路径/文件夹打开为工作空间
- **会话能力** - 上下文使用仪表、手动/自动压缩、新会话自动存档、历史会话恢复、服务重启自动找回会话
- **Agent管理** - 创建/切换/删除/编辑配置，soul/memory/tools/usage 四文件在线编辑
- **模型配置** - 模型 CRUD、嵌入/重排序模型、备用模型（main/vision 排序管理）
- **知识库管理** - 文件上传/按路径添加、删除、重建索引、向量状态
- **MCP管理** - 服务器增删、真实连接状态、逐工具开关、在线刷新
- **更多** - 技能激活、工具开关（即时生效）、向量索引管理、全局设置

启动方式：
```bash
# 启动Web服务
cbhcli web

# 指定端口
cbhcli web -p 18888
```

### 一次性非交互执行（headless 模式）
`cbhcli exec` 对标 `claude -p` / `codex exec`：终端一次性下发任务 -> ReAct 工具循环自动完成 -> 结果输出到 stdout -> 进程退出。适用于 shell 脚本、CI/CD、管道集成。

```bash
cbhcli exec "列出当前目录的 py 文件并统计总行数"           # 默认 yolo 零确认 + 默认静音
echo "总结这些提交" | cbhcli exec                        # stdin 管道，stdout 只含最终结果
cbhcli exec -v "任务..."                                 # --verbose 显示过程输出(走 stderr)
cbhcli exec --agent main --model glm-4.7 --mode auto "..."  # 指定 Agent/模型/权限模式
cbhcli exec -c "继续上次任务"                               # 续接最近一次会话
cbhcli exec --resume <SESSION_ID> "..."                     # 恢复指定会话后执行
cbhcli exec --output-format json "..." | jq .result         # 结构化输出供脚本消费
```

- **stdout/stderr 分离**：默认静音，stdout 只含最终回答；`--verbose/-v` 时过程输出实时走 stderr（终端同屏不重复打印最终回答，管道场景仍保证 stdout 有结果）
- **默认权限模式 yolo**（零确认放行），收紧用 `--mode standard/auto/readonly`（此时需确认的操作一律拒绝，退出码 2）
- **交互工具自动禁用**：ask_user 等在 headless 下从工具列表移除，全程不会阻塞等待终端输入
- **会话默认保存**到 history（`--no-save` 关闭），配合 `-c`/`--resume` 可多步续接
- **退出码**：0=成功 | 1=执行错误 | 2=用法错误或权限拒绝 | 130=用户中断

### 高级功能
- **多步规划** - 复杂任务自动拆解为 Todo 计划列表，逐步执行并追踪进度
- **自我反思** - 工具执行失败时自动分析原因并重试（最多3次）
- **子Agent协作** - 将独立子任务委托给子Agent执行，拥有独立上下文；多个独立子任务可并行委托，全部完成后主Agent再继续
- **技能系统** - 创建可复用的技能（提示词+脚本），按需激活增强AI能力
- **嵌入模型支持** - 可配置专用嵌入模型API（OpenAI compatible）
- **重排序服务** - 支持Jina、Cohere等重排序API提高检索质量
- **自动上下文压缩** - 当接近模型限制时自动压缩上下文
- **多模型支持** - 配置多个OpenAI兼容的AI模型，随时切换
- **备用模型自动切换** - 主模型断网/异常时自动切换到备用模型继续任务，视觉模型同理

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
pip install dist/cbhcli-5.3.0-py3-none-any.whl
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
/resume           # 列出历史会话（最近50条）
/resume 1         # 恢复第1个历史会话
/resume 打包      # 按标题关键词搜索历史会话
/history          # 查看历史会话列表
/comp             # 手动压缩上下文
/comp 保留迁移方案，丢弃调试过程   # 带指令压缩
/undo-compress    # 撤销最近一次上下文压缩（恢复压缩前原始消息）
/ctx              # 查看上下文使用情况
```

### 权限与安全（Harness）

```
/mode                      # 查看权限模式（readonly/standard/auto/yolo）
/mode auto                 # 切换到自动模式（yolo 需二次确认）
/permissions list          # 查看权限规则
/permissions add allow terminal(pytest:*)   # 添加永久放行规则
/hooks list                # 查看生命周期钩子
/hooks reload              # 重载 hooks.json
/undo                      # 回滚最近一次 write/edit
/undo list                 # 查看可回滚备份
/undo <ID>                 # 回滚指定备份
```

**快捷键**: `Shift+Tab` 循环切换权限模式（YOLO 需 3 秒内再按一次确认）；`Ctrl+R` 切换工具显示详细/简洁

### 7. 技能系统
技能是可复用的提示词+可选脚本，用于增强Agent在特定领域的能力。

```
/skills list              # 列出所有技能
/skills add [name]        # 创建新技能
/skills use [name]        # 激活技能（可多选）
/skills off [name]        # 取消激活
/skills rm <name>         # 删除技能
```

你可以直接让AI帮你创建技能，例如："帮我创建一个代码审查技能"。

### 8. 工具管理
每个Agent可以独立控制27个内置工具（14个通用+13个数据科学）的开关状态，关闭的工具AI将无法调用。
通用工具默认开启，cbhpacks数据科学工具默认关闭。

```
/tools list          # 查看当前Agent的工具开关状态（✅启用/❌禁用）
/tools on            # 开启已禁用的工具（交互式多选，逗号分隔编号）
/tools off           # 关闭已启用的工具（交互式多选，逗号分隔编号）
```

```
/tools list          # 查看当前Agent的工具开关状态（✅启用/❌禁用）
/tools on            # 开启已禁用的工具（交互式多选，逗号分隔编号）
/tools off           # 关闭已启用的工具（交互式多选，逗号分隔编号）
```

- 工具开关是 **per-agent 隔离** 的，每个Agent有独立的配置
- 关闭工具后，AI的 Function Calling 和系统提示中将不再包含该工具
- 关闭工具会自动更新该Agent工作空间的 `tools.md` 和 `usage.md`
- 使用 `/tools list` 可随时查看当前状态

### 9. 备用模型管理
当主模型断网或出现异常时，自动切换到备用模型继续任务。视觉模型（image工具）同理。

```
/fallback list                          # 查看备用模型配置
/fallback add main gpt-4o               # 添加主模型备用
/fallback add vision qwen-vl            # 添加视觉模型备用
/fallback rm main gpt-4o               # 移除主模型备用
/fallback reorder main                  # 重新排序主模型备用
/fallback clear vision                  # 清空视觉模型备用列表
```

- **main** - 主模型备用：主模型调用失败时按顺序自动切换
- **vision** - 视觉模型备用：image工具的视觉模型不可用时按顺序自动切换
- 备用模型必须已通过 `/model add` 配置
- 视觉备用模型必须支持视觉功能（添加时选择 vision=y）

### 10. memory.md 长期记忆
memory.md 用于保存用户要求记住的重要信息：
- **不会自动写入**：只有用户明确要求"记住"、"记录"时才写入
- **始终包含在系统提示中**：每次对话都会读取 memory.md 内容
- 普通对话历史通过 `/history` 和 `/resume` 管理

### 11. Python 工具
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
| `/model config` | 修改模型参数 |
| `/model embedding` | 配置嵌入模型 |
| `/model rerank` | 配置重排序模型 |
| `/reset` 或 `/new` | 创建新会话（自动保存当前会话） |
| `/resume [编号\|关键词]` | 列出或恢复历史会话（关键词按标题搜索） |
| `/history` | 查看历史会话列表 |
| `/comp [指令]` | 压缩上下文（可带保留/丢弃指令） |
| `/undo-compress [编号]` | 撤销最近一次上下文压缩（恢复压缩前原始消息） |
| `/ctx` | 查看上下文使用 |
| `/mode [模式]` | 权限模式切换（readonly/standard/auto/yolo） |
| `/permissions [list\|add\|rm]` | 权限规则管理 |
| `/hooks [list\|reload\|test]` | 生命周期钩子管理 |
| `/undo [ID\|list]` | 回滚 write/edit 文件修改 |
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
| `/tools list` | 查看当前Agent的工具开关状态 |
| `/tools on` | 开启工具（交互式多选） |
| `/tools off` | 关闭工具（交互式多选） |
| `/fallback list` | 查看备用模型配置 |
| `/fallback add [main\|vision] <模型名>` | 添加备用模型 |
| `/fallback rm [main\|vision] <模型名>` | 移除备用模型 |
| `/fallback reorder [main\|vision]` | 重新排序备用模型 |
| `/fallback clear [main\|vision]` | 清空备用模型列表 |
| `/chain list` | 列出所有 Agent 链条 |
| `/chain add <名称>` | 交互式创建链条 |
| `/chain use <名称>` | 激活链条 |
| `/chain off` | 取消链条绑定 |
| `/chain show <名称>` | 查看链条详情 |
| `/chain rm <名称>` | 删除链条 |
| `/chain config <名称>` | 编辑链条配置 |
| `/chain rename <旧名> <新名>` | 重命名链条 |
| `/help [command]` | 显示帮助 |
| `quit` | 退出程序 |

### 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Enter` | 发送消息 / 执行命令 |
| `Alt+Enter` | 输入框换行 |
| `Ctrl+J` | 输入框换行 |
| `Ctrl+R` | 切换工具显示详细/简洁模式 |
| `Tab` | 补全菜单选择下一项 |
| `↑` / `↓` | 补全菜单上下选择 |

## 项目结构

```
cbhcli_pkg/
├── core/              # 核心模块
│   ├── app.py              # 主应用
│   ├── input_box.py        # 聊天输入框组件（原生补全系统）
│   ├── text_width.py       # 字素簇宽度计算（emoji/CJK精确对齐）
│   ├── resize_fix.py       # 终端resize防抖（防输入框重复显示）
│   ├── prompt_utils.py     # 统一交互输入（替代内置input）
│   ├── agent.py            # Agent管理
│   ├── session.py          # 会话管理
│   ├── session_history.py  # 会话历史管理
│   ├── model.py            # LLM客户端
│   ├── ai_handler.py       # AI请求处理（Function Calling + 反思）
│   ├── tool_executor.py    # 工具执行
│   ├── subagent.py         # 子Agent调度器（支持并行执行）
│   ├── skill_manager.py    # 技能管理器
│   ├── response_cleaner.py # 响应清理
│   ├── embedding_client.py # 嵌入模型客户端
│   ├── rerank_client.py    # 重排序客户端
│   ├── knowledge_base.py   # 知识库管理
│   ├── mcp_client.py       # MCP协议客户端
│   ├── mcp_manager.py      # MCP服务器管理
│   ├── mcp_tool_adapter.py # MCP工具适配器
│   ├── permissions.py      # 权限规则引擎（4模式，Harness 治理层）
│   ├── loop_detector.py    # 死循环检测（工具重复/文本复读）
│   ├── hooks.py            # Hooks 钩子系统（生命周期6事件）
│   ├── tracer.py           # 调用链追踪（JSONL 落盘）
│   ├── checkpoint.py       # 文件检查点（write/edit 自动备份/回滚）
│   ├── spinner.py          # 终端加载动画（工具执行spinner）
│   ├── constants.py        # 常量定义
│   └── errors.py           # 异常类型
├── tools/             # 工具实现
│   ├── terminal.py     # 终端命令执行
│   ├── file_read.py    # 文件读取
│   ├── file_write.py   # 文件写入
│   ├── file_edit.py    # 精确字符串替换
│   ├── grep.py         # 正则搜索
│   ├── glob_tool.py    # 文件模式搜索
│   ├── ask_user.py     # 用户提问交互
│   ├── todo.py         # 任务计划列表
│   ├── python_tool.py  # Python执行（带会话记忆）
│   ├── memory_search.py # 记忆搜索
│   ├── knowledge_base.py # 知识库查询
│   ├── delegate_task.py  # 子Agent任务委托（串行/并行）
│   ├── image.py         # 图片识别（调用视觉模型）
│   ├── skills_create.py  # 技能创建
│   ├── base.py          # 工具基类
│   └── registry.py      # 工具注册中心
├── commands/          # 斜杠命令
│   ├── parser.py       # 命令解析器
│   ├── agent_cmd.py    # Agent管理命令
│   ├── model_cmd.py    # 模型管理命令
│   ├── session_cmd.py  # 会话管理命令
│   ├── kb_cmd.py       # 知识库命令
│   ├── embedding_cmd.py # 向量索引命令
│   ├── mcp_cmd.py      # MCP管理命令
│   ├── skills_cmd.py   # 技能管理命令
│   ├── tools_cmd.py    # 工具开关管理命令
│   ├── fallback_cmd.py # 备用模型管理命令
│   └── harness_cmd.py  # Harness命令（/mode /permissions /hooks /undo）
├── web/               # Web界面
│   ├── server.py       # FastAPI 后端（WebSocket 实时 + 会话后台运行 + 全量管理 API）
│   └── static/         # 原生前端 SPA（index.html + css + js + vendor/marked/katex/mermaid/echarts，零构建）
├── config/            # 配置管理
│   └── global_config.py # 全局配置
├── context/           # 上下文管理
│   ├── compressor.py   # 上下文压缩器
│   └── token_counter.py # Token计数器
└── vector/            # 向量数据库
    ├── store.py        # ChromaDB封装
    └── indexer.py      # 记忆索引器

docs/                 # 项目文档
├── API参考/           # API开发文档
├── Agent管理/         # Agent管理指南
├── 会话管理/          # 会话管理文档
├── 工具系统详解/       # 工具开发文档
├── 命令参考手册/       # 命令参考
├── 开发者指南/         # 开发文档
├── 快速开始.md
└── ...

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
