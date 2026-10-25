# CBHCLI v5.5.1 - AI驱动的终端助手

一个功能强大的AI驱动终端助手，支持多Agent管理、工具调用、知识库和会话管理。

## 特性

### 核心功能
- **全屏 TUI（v5.5.0，默认界面）** - 主区滚动历史 + 固定输入区 + 底部状态栏（含上下文占用条 `█░`）：思考窗口/工具输出/spinner 全部在主区内按新宽度重排，终端 resize 不再依赖"估算行数 + 相对擦除"；命令向导、工具确认、`ask_user`、`/login` 密钥均为应用内模态（菜单可见、密码不回显）；`Ctrl+C` 五级状态机（模态 > 工具 > AI 请求 > 命令 > 空闲）；退出时回填最近 200 行 + 完整 transcript 落盘（`~/.cbhcli/agents/<agent>/history/transcripts/`）；`CBHCLI_UI=legacy` 可切回旧内联界面
- **v5.5.1 修复与体验（2026-10-25）**：
  - **上下文压缩重写**：按 user 消息边界切分"轮"（不再按"轮数×2 条消息"估算）→ 保留**最近 N 轮用户请求**及其完整工具链，预算不足时**优先牺牲最早轮**；压缩目标自适应（不小于系统提示+摘要下限+最小保留，且 ≤ 窗口 70%）；压缩后消息顺序固定为「主系统提示 → 摘要 system → 保留轮」，符合严格 API 的 `system must be at the beginning`；
  - **修复压缩后 API 400 `Invalid \uXXXX escape`**：旧实现按字符截断工具参数会把 JSON 切断 → 现改为**结构化截断**（保证合法 JSON），并在发送前对 `tool_calls.arguments` 做合法性自愈；
  - **修复 `/comp` 误报"上下文较短"**：轮数不多但上下文很长时也能压缩（自动让出最早轮），失败时给出具体原因；
  - **所有 rich 渲染按主区宽度出图**（Markdown 正文/代码预览/子Agent状态板/欢迎面板/思考窗）：统一 `text_width.render_width()`，杜绝"最右 1~2 列被折到下一行左侧"的错行；
  - **选区保护**：鼠标操作后 0.6s 内不重绘（终端收到输出会清掉原生选区）；关闭 `?1015` 只保留滚轮+点击；
  - **滚动跟随**：发送消息自动回到底部；手动上翻保持阅读位置（新内容到达时同步补偿）；25s 无操作自动回到底部。
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
- **聊天输入框** - 基于 prompt_toolkit 原生补全系统，字素簇宽度感知，支持中英文/emoji（含ZWJ/VS16/旗帜/肤色）输入退格无错位、终端缩放抗 reflow 防重复（重绘范围与清除范围严格一致）、斜杠命令补全菜单
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

- **用户认证与审计系统（v5.4.0）** - 内网多用户认证 + 全链路审计：① **四端统一认证**（CLI / Web / JupyterLab 插件 / exec 共享 `~/.cbhcli/auth.json` 设备级凭据，**登录一次四端通行**；未登录时：CLI 允许进入但发消息拦截（可 `/login` 就地登录，登录后无需重启）、Web 显示登录界面、exec 退出码 2、Jupyter 全 API 401 + 提示横幅）；② **管理服务 cbhcli-admin**（默认端口 **28888**）：主密码启动门（内置默认 / 环境变量 / admin.json 覆盖，连错 5 次退出）+ 管理页面 7 视图（登录 / 仪表盘 / 用户管理 / 会话管理 / 实时监控 / 审计日志 / 设置）；用户创建（**密钥随时可查看/复制**：前缀 + `****` 掩码 + 👁/📋 按钮）/ 续期 / 重置密钥 / 禁用启用 / 删除；**管理页面实时化**：WebSocket 实时交互画面（思考/正文/工具调用秒级上屏）+ 运行中会话全程可见（多进程心跳按实例隔离，互不覆盖）；③ **三段式审计上报**：状态流（60s 心跳，"运行中"秒级上屏）/ 事件流（工具调用/权限拦截/模式切换等，2s 批报）/ 会话快照（每轮去重上传，**本地删除后管理端仍保留**）；断网"软在线"+ 落盘补传（eid 幂等去重）；④ **有效期策略**（7/30/90/365 天 / 自定义 / **永久**）+ 剩余 <2 天滚动续期 + 管理页面一键续期（已过期用户无感恢复）；⑤ 命令：`cbhcli login/logout/whoami/ping/admin`、`cbhcli-admin [status]`，界面内 `/login /logout /whoami /ping`（详见「用户认证与审计系统」章节）

### 17大内置工具 + Web 专属工具
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
| `send_file` 📡 | **仅 Web 端**：向用户发送文件/图片，图片内联显示、文件下载链接 |

### cbhpacks 数据科学工具（14个，默认关闭）
基于 [cbhpacks](https://github.com/chenbh17/cbhpacks) 数据科学工具包封装，覆盖完整的机器学习建模流水线。默认不开启，通过 `/tools on` 手动开启。

**会话级状态缓存（v4.9.3）**：与 python 工具共享会话命名空间 —— 各工具的执行结果变量（如 `bm`/`woe_data`/`iv_data`/`mt`/`clf`/`selected_cols`/`data`）自动注入会话，可在 python 工具中直接使用做二次分析；同参数重复调用自动复用缓存实例（如 fit 后的模型可直接调参/出报告）。`/new` 或 `/reset` 后所有缓存与变量自动释放。

**建模护栏（v5.3.1 Harness）**：关键节点由规则代码**确定性检查**（不依赖 AI 自觉）——分箱（comp_woe_iv/bins_rpt/get_psi）、训练（fit/调参/report）自动注入 A/B/C/D/E 组检查；`cbhpacks_harness` 工具可显式调用全部检查。警告经 CLI 醒目横幅 / Web 卡片徽标+警告块 / PostToolUse hooks 三通道可见，写入 `history/traces/*.jsonl` 审计。数据穿越铁律：**WOE 只在训练集计算**（训练集 data_to_woe 省略参数自动继承 comp_woe_iv 分箱参数；测试集/OOT 必须传 woe_mapping_pkl 用训练集映射，防穿越）。

**护栏假阴性防护（同版本内补强）**：
- **A0 预填充检测**：check_data 须传原始含 NaN 数据 + nan 参数；传已 fillna 的数据时数据中无缺失信息、填充值安全性无法评估，工具会显式提示（不再静默"全部通过"）
- **D0/E0 输入结构校验**：check_overfit/check_stability 声明的输入须为真实工具链产出（report 的 confusion_matrix_*.xlsx、get_psi 的 psi_single_rpt_*.xlsx）；手工模拟/缺标准列（type[auc/ks]、psi）的文件会触发结构警告且 D1/E1 不执行，杜绝假阴性
- **C5 三层泄漏检测**：完美泄漏特征（如 target×10）经 adj_bin 分箱后 WOE/IV 被置 0"洗白"、仅看 iv_data 必漏报——① 从 woe_data 分箱明细重算**修正 IV**（单侧箱 ±0.5 连续化，结果注入 `corrected_iv` 变量）；② **原始数据级点二列相关**（|r|>0.9，不受任何分箱影响）终极兜底；③ comp_woe_iv / check_leakage 内置①+②自动报警
- **data_to_woe 支持 output_csv**：WOE 转换结果可指定另存路径（如 step3_woe/train_woe.csv），下游训练直接作 train_csv（默认仍写 output_path/woe_transformed_data.csv）
- **get_psi 月份预校验**：cmp_mth/base_mth 不在数据月份列中时明确报错并列出可用月份（替代裸 KeyError），或建议改用 psi_mth_avg 自动遍历全部月份

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
| `cbhpacks_harness` | 建模护栏校验（v5.3.1，含 A0/D0/E0 防假阴性 + C5 三层泄漏检测） | check_data, check_bins, check_leakage, check_overfit, check_stability, check_all |

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
- **实时对话（WebSocket）** - 流式渲染 Markdown、思考块折叠、工具调用卡片（默认收起，失败/含图自动展开）、工具确认条（允许/拒绝/全部允许/始终允许该命令）、ask_user 交互问答、自我反思重试提示、工具运行中随时中断；**多个浏览器打开同一会话画面完全一致**，多浏览器同时弹出确认时首个应答生效；**滚动跟随**（用户往上翻阅历史时不再被流式输出强制拽到底部，右下角"回到底部"按钮一键返回）
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

## 界面模式（v5.5.0）

| 档位 | 启用方式 | 说明 |
|---|---|---|
| **fullscreen（默认）** | 直接运行 `cbhcli` | 全屏 TUI：主区滚动 + 固定输入区 + 状态栏；思考/工具/spinner 统一在主区重排；应用内模态处理向导与确认；退出回填最近 200 行 + transcript |
| legacy | `CBHCLI_UI=legacy cbhcli` | 旧内联界面（PromptSession + 相对擦除），保留全部旧行为，用于对照/降级 |
| sink | `CBHCLI_UI=sink cbhcli` | 诊断用：仅安装输出代理（字节级透传），界面与 legacy 相同 |

- **非 TTY / `TERM=dumb` 自动回退 legacy**（管道、CI、`exec` 不受影响）。
- transcript 路径：`~/.cbhcli/agents/<agent>/history/transcripts/<日期>_<会话ID>.log`（0600，思考/spinner 不写入）。
- 主区滚动：**鼠标滚轮**（每次 3 行）、`PgUp/PgDn`、`Ctrl+Home/End`，`Ctrl+L` 清空主区。
- 选中文本：鼠标捕获期间按住 **Shift 再拖动**（终端原生选中）——**先松开鼠标左键，再松开 Shift**（反过来终端会取消选区）。
- **不再提供 F2 开关/无鼠标模式**：滚轮始终开启；`CBHCLI_MOUSE` 与 `~/.cbhcli/ui.json` 已不再使用。
- 命令补全菜单为**单列显示**（一行一个命令，不再多列平铺）。
- 每轮对话结束会自动整屏重绘一次（清除渲染残格）；若当时正在拖选（左键按下/刚有鼠标动作）会自动顺延，避免清掉你的选区。
- 状态栏：会话正在执行（AI 请求 / 命令）时，最左侧显示**动态 spinner + `运行中`**（`⠋⠙⠹⠸…`，约 1 秒转一圈），空闲后自动消失；**交互式提问（/resume、/model、/login 等）等待输入时不显示**。
- 交互提问：提示与输入在同一行（`模型名称: 输入内容`），与旧 PromptSession 一致。
- 退出时显式复位鼠标协议（`1000/1002/1003/1006/1015/1007`），恢复原终端滚轮与拖选。

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
pip install dist/cbhcli-5.5.1-py3-none-any.whl
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
# 0. 首次使用先登录（向管理员获取【服务器地址 + 认证密钥】；登录一次，四端通行）
cbhcli login 192.168.1.100:28888 cbhk_xxxxxx

# 启动应用
cbhcli

# 查看帮助
/help

# 查看版本
cbhcli --version
```

> 服务器端（管理员）先启动管理服务并创建用户：`cbhcli admin`（输入管理员主密码）→
> 浏览器打开 `http://<服务器IP>:28888/admin` → 用户管理 → 创建用户 → 把密钥发给用户
> （密钥在用户列表中可随时通过 👁 眼睛查看 / 📋 复制，不会"关闭后无法再查看"）。
> 详见「[用户认证与审计系统](#用户认证与审计系统v540)」章节。

## 使用指南

### 1. 配置模型
```
/model add
# 按提示输入: 模型名称、API Key、Base URL、模型ID、上下文长度

/model list    # 查看已配置的模型
/model use     # 交互式选择模型
```

> **同一模型 ID 可配置多份不同参数**（例如一个开启思考、一个关闭思考，
> 或不同 temperature / max_tokens）：`/model add` 时**名称填不同**（如
> `deepseek-flash-有思考` / `deepseek-flash-无思考`），**模型 ID 填同一个**即可。
>
> - 「模型名称」是模型配置的唯一标识：`/model use`、`/fallback`、`/agent` 选模型
>   以及 Web/Jupyter 的模型选择器都按名称区分
> - `/model list` 的「◀ 当前使用」标记、底部状态栏的「模型:」、欢迎面板与 `/ctx`
>   显示的都是**当前使用的配置名称**（`/model info` 中另行列示真实的模型 ID）
> - 注意：`thinking=off` 时不能配置 `reasoning_effort`（DeepSeek 等 API 会返回 400）

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
/resume 1         # 恢复第1个历史会话（恢复后回显历史对话内容）
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
# 带认证（值含空格时用引号包裹）：
/mcp add authed http://localhost:8080/mcp Authorization="Bearer xxx"
# 名称可省略（自动从 URL 推导）：
/mcp add https://dashscope.aliyuncs.com/api/v1/mcps/TextGenerateImage/mcp Authorization="Bearer ${DASHSCOPE_API_KEY}"
```
说明：header 值支持 `${环境变量}` / `$环境变量` 占位符，连接时展开（mcp.json 中不落明文 Key）；变量未设置时连接失败信息会明确提示。

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
| `/resume [编号\|关键词]` | 列出或恢复历史会话（关键词按标题搜索；恢复后回显历史对话） |
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
| `/mcp add [名] <URL> [header=值]` | 添加 MCP 服务器（名可省略；值含空格用引号；支持 ${ENV}） |
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
| `/login [IP:端口] [密钥]` | 用户登录（向导式：地址 → 连通性测试 → 密钥） |
| `/logout [--local-only]` | 退出登录（吊销凭据，四端失效） |
| `/whoami` | 查看当前登录身份与凭据状态 |
| `/ping [IP:端口]` | 服务器连通性测试 |
| `/help [command]` | 显示帮助 |
| `quit` | 退出程序 |

### 认证与审计命令（v5.4.0）

| 命令 | 功能 |
|------|------|
| `cbhcli login` | 登录（交互向导；也支持 `cbhcli login <IP:端口> <密钥>` 一次性参数） |
| `cbhcli logout [--local-only]` | 退出登录（吊销服务端 token + 删除本地凭据） |
| `cbhcli whoami` | 查看当前登录身份与凭据状态 |
| `cbhcli ping [服务器] [--json]` | 连通性测试（延迟/版本/协议兼容/登录状态；未登录也可用） |
| `cbhcli admin` | 启动管理服务（服务器端，需管理员主密码；端口默认 28888） |
| `cbhcli admin status` / `cbhcli-admin status` | 管理服务自检（端口/版本/运行时长/最近上报） |
| `cbhcli-admin` | 管理服务独立入口（等价 `cbhcli admin`，供 systemd/脚本调用） |

> 认证说明：CLI / Web / JupyterLab 插件 / exec 四端统一认证，**登录一次即全部可用**；
> 凭据保存在 `~/.cbhcli/auth.json`（0600，设备级）。未登录时：CLI 发消息被拦截（可 `/login` 就地登录）、
> Web 显示登录界面、exec 退出码 2、Jupyter 插件 401 提示。凭据过期请联系管理员续期（续期后重试自动恢复）。
>
> 📖 **完整用法（含 cbhcli-admin 管理服务、管理页面、运维场景）见下方
> [「用户认证与审计系统」](#用户认证与审计系统v540) 章节。**

### 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Enter` | 发送消息 / 执行命令 |
| `Alt+Enter` | 输入框换行 |
| `Ctrl+J` | 输入框换行 |
| `Ctrl+R` | 切换工具显示详细/简洁模式 |
| `Tab` | 补全菜单选择下一项 |
| `↑` / `↓` | 补全菜单上下选择 |

---

## 用户认证与审计系统（v5.4.0）

> 内网多用户环境下的**用户认证 + 全链路审计**：CLI / Web / JupyterLab 插件 / exec **四端统一认证**
> （登录一次，四端通行），配套管理服务 **cbhcli-admin**（用户管理 / 会话浏览 / 实时监控 / 权限查看 / 审计日志）。

### 一、管理服务端（cbhcli-admin）

#### 1. 启动与主密码

```bash
cbhcli admin                  # 交互式：提示输入管理员主密码（不回显），通过后启动
cbhcli-admin                  # 等价入口（供 systemd / 脚本调用）
```

**启动门（第一层安全）**：主密码校验源优先级
1. **已修改的主密码**（`admin.json` 中 `key_source=explicit`）—— 经由管理页面
   「修改主密码」或 `cbhcli admin --set-password` 写入，**最高优先**；
   修改后**用新密码登录即可**（立即生效、无需重启；也无需同步启动配置）
2. 环境变量 `CBHCLI_ADMIN_MASTER_KEY`（未显式修改过时的部署凭据，用于无人值守/开机自启）
3. 数据目录 `~/.cbhcli-admin/admin.json` 中引导写入的 `master_key_hash`（首次成功启动后自动写入）
4. 代码内置默认口令 —— **仅以 hash 存储（源码/whl 中不含明文）**，口令不公开；
   实际部署请通过 ① 或 `--set-password` 设置自己的口令

连续输入错误 **5 次**直接退出（退出码 2）；非交互环境（stdin 非 TTY）启动必须提供环境变量，否则拒绝启动。
（若主密码已修改过而启动配置里的环境变量还是旧值：仅打印提示并以已修改的主密码继续启动——
管理页面用新主密码登录；`--set-password` 修改密码时必须先验证当前密码，无跳过校验的通道。）

```bash
# 无交互启动（systemd / crontab / 容器）—— 用你自己的主密码
CBHCLI_ADMIN_MASTER_KEY=<你的主密码> cbhcli-admin --port 28888
```

**全部启动参数**：

| 参数 | 说明 | 默认 |
|------|------|------|
| `--port, -p PORT` | 管理服务端口 | 28888 |
| `--host HOST` | 监听地址 | 0.0.0.0 |
| `--data-dir PATH` | 数据目录（db/快照/配置） | `~/.cbhcli-admin` |
| `--tls-cert CERT` / `--tls-key KEY` | 启用 HTTPS（自签场景客户端需信任证书） | 关闭（内网 HTTP 基线） |
| `--set-password` | 交互式修改管理员主密码（需当前密码，立即生效） | - |
| `status` | 服务自检（不启动服务） | - |
| `-h, --help` | 帮助 | - |

#### 2. 服务自检

```bash
cbhcli-admin status          # 等价 cbhcli admin status
```

运行中输出：端口（监听状态）/ 版本（protocol）/ 启动时间（已运行时长）/ 数据目录（DB 大小 / 快照数）/ 最近上报时间；
未运行时提示启动命令；检测到 `server.json` 残留（进程已死）会给出清理提示。

#### 3. 数据目录 `~/.cbhcli-admin/`

```
~/.cbhcli-admin/
├── admin.json         # 主密码 hash + 服务设置（strict_online / report_level / 默认有效期…），0600
├── cbhcli-admin.db    # SQLite（users / tokens / sessions / events / logins）
├── snapshots/         # 会话快照 <user_id>/<session_id>.json（管理端完整查看会话内容）
├── backup/            # 数据库每日自动备份（保留 30 份）
├── server.json        # 运行信息（pid/port/ts/version，status 自检用；退出时清除）
└── boot.log           # 开机自启日志（配置 crontab 后产生）
```

#### 4. 管理页面（7 个视图）

浏览器打开 **`http://<服务器IP>:28888/admin`** → 输入主密码登录（admin_token 存 sessionStorage，24h 过期）。

| 视图 | 功能 |
|------|------|
| 登录页 | 主密码登录（失败限流：同 IP 5 次/分钟） |
| 仪表盘 | 统计卡片（用户/在线/今日会话/运行中）+ 服务健康卡片（运行时长/版本/DB大小/最近上报 + 自检按钮）+ 实时活动流（3s 刷新）+ 在线用户列表 |
| 用户管理 | **创建用户**（有效期选择：7/30/90/365 天、自定义、**永久**）；密钥列（前缀 + `****` 掩码，**👁 眼睛查看完整密钥 / 📋 一键复制**，创建后随时可取，无需重置）；**续期** / **重置密钥** / 权限快照（**禁用工具全量显示——整个列表单一横向滚动条，不截断**）/ 禁用启用 / 删除（可选含会话数据）；跳转该用户会话 |
| 会话管理 | 全部用户会话（筛选：用户/状态/关键词；**列表实时更新**——新会话/运行中状态 WS 秒级上屏，无需手动刷新）+ 详情抽屉（消息流回放：**思考 → 文字 → 工具调用**；**运行中会话 WebSocket 实时画面**——思考/正文/工具调用流式上屏，轮结束自动并入快照）+ 删除 |
| 实时监控 | 运行中会话卡片墙（用户/Agent/标题/工作空间/时长/最后心跳颜色）；**会话运行全程持续显示**（多进程心跳按实例隔离，互不覆盖）+ **点击卡片 → WebSocket 实时交互画面**（与 Web 端一致的实时流）；WS 推送即时刷新 + 2s 轮询兜底 |
| 审计日志 | 事件流（时间/用户/类型徽标/会话/摘要；筛选：类型/用户/时间范围；分页）+ 登录记录（成功/失败/原因）+ **导出 CSV** |
| 设置 | strict_online 开关 / auto_renew 开关 / report_level（full/summary，summary 跳过实时流式内容）/ 新用户默认有效期 / 严格模式宽限 / 快照上限 / 服务自检 / 修改主密码（立即生效，其它管理会话失效） |

#### 5. 管理 API 参考（请求头 `X-Admin-Token`）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/admin/api/login` | 管理员登录（主密码）→ admin_token |
| GET | `/admin/api/overview` | 仪表盘统计 + 最近事件 |
| GET | `/admin/api/health` | 健康自检（运行时长/DB/磁盘/最近上报） |
| GET | `/admin/api/live` | 实时快照（在线用户 + 运行中会话） |
| GET / POST | `/admin/api/users` | 用户列表 / 创建用户（返回密钥；列表仅含前缀 + `key_has_plain`） |
| PUT / DELETE | `/admin/api/users/{id}` | 更新（禁用/备注/有效期策略）/ 删除（`?purge=1` 级联删除会话数据） |
| POST | `/admin/api/users/{id}/reset_key` | 重置密钥（key_version+1，旧 token 全失效） |
| POST | `/admin/api/users/{id}/renew` | 续期 `{"ttl_days": 7\|30\|90\|365\|"forever"}` |
| GET | `/admin/api/users/{id}/permissions` | 该用户权限快照（模式/规则/禁用工具/活跃链条） |
| GET | `/admin/api/users/{id}/key` | 查看用户完整密钥（管理页面「👁 眼睛」；升级前创建的用户 `available=false` 仅可看前缀） |
| WS | `/admin/ws?token=<admin_token>` | **实时事件推送**（管理页面实时交互画面：轮开始/思考/正文 `stream_chunk`/工具调用/状态；断线自动重连） |
| GET | `/admin/api/sessions` | 会话列表（筛选：user/status/q/时间；分页） |
| GET / DELETE | `/admin/api/sessions/{user_id}/{session_id}` | 会话详情（快照 JSON）/ 删除 |
| GET | `/admin/api/events` / `/admin/api/logins` | 事件流 / 登录记录（筛选 + 分页） |
| GET | `/admin/api/export/events.csv` / `/admin/api/export/sessions.csv` | 导出 CSV |
| GET / PUT | `/admin/api/settings` | 服务设置（strict_online / auto_renew / report_level / 默认有效期 / 宽限 / 快照上限；服务端校验范围，非法值 400） |
| POST | `/admin/api/settings/password` | 修改主密码（需当前密码；立即生效；返回 `warning` 提醒同步更新环境变量） |

#### 6. 开机自启（crontab 示例）

```
#[14] cbhcli-admin 管理服务（用户认证/审计，端口28888）
@reboot nohup bash -c 'CBHCLI_ADMIN_MASTER_KEY=<主密码> /home/<user>/.local/bin/cbhcli-admin --port 28888 --host 0.0.0.0' > ~/.cbhcli-admin/boot.log 2>&1 &
```

> 💡 用环境变量提供主密码（开机时无法交互输入）。**主密码一经修改，即以其为准**——
> 改密码后用新主密码登录即可，**无需修改这一行**（服务自动处理：进程内环境变量自动
> 同步；启动时若检测到旧值仅打印提示，仍以新主密码继续启动）。
> 无命令级"重置"通道（安全设计）——忘记主密码时可用当前密码交互式启动，或由服务器管理员
> 删除 `~/.cbhcli-admin/admin.json` 中的 `master_key_hash`/`master_key_salt`/`key_source`
> 三个字段后重启（回落环境变量/内置默认；需服务器文件权限，即最高权限边界）。

#### 7. 备份与运维

```bash
cbhcli-admin status        # 日常自检
# 停止:  ss -ltnp | grep 28888  → 取 pid → kill <pid>
# 备份:  整个 ~/.cbhcli-admin/ 目录（db + snapshots + admin.json）
```
- 数据库启动时自动每日备份到 `~/.cbhcli-admin/backup/`（保留 30 份）
- 快照含完整对话内容（可能含业务敏感信息）→ 注意磁盘加密与目录权限（服务端数据目录 0700）

#### 8. 常见运维场景

| 场景 | 操作步骤 |
|------|----------|
| **给新同事开账号** | 管理页面 → 用户管理 → 「＋ 创建用户」（填用户名/显示名 + 选有效期）→ 复制密钥发给对方（密钥**随时可在用户列表查看/复制**，无需重置）→ 对方在**自己的机器**上运行 `cbhcli login <服务器IP>:28888 <密钥>` |
| **续期（临近到期）** | 用户管理 → 该用户行「续期」→ 选新有效期（预设/自定义/永久）→ 客户端下一次心跳自动生效（whoami 可见变化） |
| **续期（已过期，用户被拦）** | 点「续期」后通知用户**直接重试**即可自动恢复（无需重新输密钥） |
| **密钥泄露 / 定期轮换** | 用户管理 → 「重置密钥」→ 旧密钥与全部旧凭据立即失效 → 新密钥发给用户重新登录 |
| **离职 / 临时停用** | 「禁用」（保留数据，随时恢复）或「删除」（可勾选同时删除会话数据）→ 该用户心跳与上报立即被拒 |
| **查看某人跑了什么** | 会话管理 → 按用户筛选 → 点击行打开详情（消息流/工具调用/思考过程）；审计日志 → 按用户/类型/时间筛选，可导出 CSV |
| **实时盯运行中任务** | 实时监控页（运行中会话卡片墙，WS 即时刷新）→ **点击卡片打开实时交互画面**（思考/正文/工具调用实时上屏，和 Web 端一致）；运行中会话**全程持续显示**不闪烁 |
| **排查"无法登录"** | ① 客户端 `cbhcli ping <服务器>`（四类诊断）② 管理页面 → 审计日志 → 登录记录（失败原因：invalid_key / disabled / rate_limited）③ 检查管理服务自检 `cbhcli-admin status` |
| **客户端整体离线使用** | 默认"软在线"，服务器短暂故障不阻塞已登录用户；如需高风控（离线超宽限即拦截）→ 设置页开启 `strict_online` |
| **修改主密码** | 管理页面 → 设置 → 「修改主密码」（需当前密码；改后**立即生效、用新密码登录即可**，其它管理会话失效）→ **无需修改启动配置**（环境变量自动让位：进程内自动同步、旧值仅提示不阻塞）；无跳过校验的重置命令（安全设计） |

### 二、客户端接入（四端）

#### 1. 登录（四种入口任选其一，登录一次四端通行）

| 入口 | 命令 / 位置 | 说明 |
|------|------------|------|
| ① shell 命令 | `cbhcli login` | 交互向导；也支持 `cbhcli login <IP:端口> <密钥>` 一次性参数 |
| ② CLI 界面内 | `/login` | 向导式（地址 → 连通性测试 → 密钥 → 成功确认），**登录后无需重启** |
| ③ Web 页面 | `cbhcli web` → 浏览器登录界面 | 含「测试连接」按钮；登录结果属于该 Web 服务所在主机 |
| ④ Jupyter 插件 | 401 提示引导 | 引导运行 `cbhcli login` 或打开 Web 登录界面 |

```bash
cbhcli login 192.168.1.100:28888 cbhk_xxxxxxxx     # 一次性参数（脚本/部署）
cbhcli login --server 192.168.1.100:28888 --key cbhk_xxx --force   # --force 跳过已登录确认
CBHCLI_AUTH_KEY=cbhk_xxx cbhcli login              # 环境变量提供密钥
```

**错误指引**（各端统一文案）：无法连接（含 telnet 排查提示）/ 密钥无效（联系管理员重置）/
账号被禁用 / 协议版本不匹配（升级 cbhcli）。

#### 2. 状态查看与退出

```bash
cbhcli whoami                # 身份 / 服务器 / 有效期（永久或剩余天数）/ 在线状态 / 设备标识
cbhcli ping                  # 连通性测试（--json 结构化输出；失败给四类诊断）
cbhcli ping 192.168.1.100:28888   # 未登录也可测试任意服务器
cbhcli logout                # 退出（吊销服务端 token + 删除本地凭据；--local-only 只删本地）
```

界面内对应命令：`/whoami`、`/ping [IP:端口]`、`/logout [--local-only]`。

#### 3. 未登录拦截行为

| 端 | 行为 |
|----|------|
| CLI | **允许进入界面**（欢迎面板显示未登录提示）；发消息被拦截（提示 `/login` 与 `cbhcli login` 双入口）；本地配置类命令（/model、/tools 等）不受限 |
| Web | 业务 API 一律 401 + 页面显示**登录视图**（浏览器内完成登录）；任意 401 自动切回登录视图；顶栏连接状态指示器（可点击重测） |
| exec | 直接拒绝（stderr 提示），**退出码 2** |
| Jupyter | 全部 API 返回 401 + 面板顶部「🔐 未登录」横幅 |

#### 4. 凭据与有效期

- 凭据文件：`~/.cbhcli/auth.json`（**0600**，只存 token 不存原始密钥；目录 0700；`CBHCLI_AUTH_FILE` 可覆盖路径）
- `expires_at: null` = **永久（无限期）** token
- **续期三条路径**：① 剩余 <2 天自动滚动续期（在线用户无感）② 管理页面「续期」一键延长（含已过期用户——续期后重试自动恢复，无需重新输密钥）③ 重新登录
- 管理员**重置密钥** → key_version+1 → 该用户全部旧 token 失效（全员踢下线）
- 管理员**禁用用户** → 心跳/上报/校验全部 401

#### 5. 审计上报机制（三段式）

| 段 | 内容 | 频率 | 用途 |
|----|------|------|------|
| 状态流 | 会话运行状态（running/idle + 心跳 + 权限快照） | 每轮 + 60s 心跳 | 管理页面"运行中"实时显示（秒级上屏） |
| 事件流 | login/logout、会话轮次、工具调用、权限拦截、模式切换、压缩、fallback | 批量 2s | 全链路审计 |
| 会话快照 | 整份会话 JSON（与本地 history 同构） | 每轮结束（去重） | 管理端完整查看（**本地删除后管理端仍保留**） |

- 一切上报**静默失败不影响主流程**；断网时本地落盘（`~/.cbhcli/report_queue/`），恢复后自动补传（eid 幂等去重）
- **断网策略（默认"软在线"）**：服务器故障不阻塞已登录用户；管理端可开 `strict_online` 严格模式（离线超宽限即拦截）
- `cbhcli exec --no-save` 只影响本地 history 落盘，**不影响审计上报**（内存构造快照上传）

### 三、安全说明（信任边界）

- **防**：内网中未认证使用、无审计使用；管理员对"谁在何时跑了什么会话"的可见性；密钥/账号生命周期管理
- **不防**：拥有本机代码修改能力的用户（客户端侧校验可被绕过）。审计数据可证明"正常路径行为"，
  不能作为对抗恶意内部攻击者的唯一证据；高安全场景应配合客户端统一分发（运维推送安装）+ 服务端网络策略
- 主密码/密钥/token 全部哈希存储（sha256+salt）；admin_token 24h 过期；
  管理操作记入 `admin_action.*` 审计事件（含登录、创建/删除用户、重置密钥、续期、改设置）

## 项目结构

```
cbhcli_pkg/
├── core/              # 核心模块
│   ├── app.py              # 主应用
│   ├── input_box.py        # 聊天输入框组件（原生补全系统）
│   ├── text_width.py       # 字素簇宽度计算（emoji/CJK精确对齐）
│   ├── resize_fix.py       # 终端 resize 修复（重绘范围=清除范围，防输入框重复显示）
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
│   ├── harness_cmd.py  # Harness命令（/mode /permissions /hooks /undo）
│   └── auth_cmd.py     # 登录命令（/login /logout /whoami /ping，v5.4.0）
├── auth/              # 客户端认证与审计上报（v5.4.0，四端共用）
│   ├── store.py        # 凭据存储（~/.cbhcli/auth.json，0600）
│   ├── client.py       # 管理服务器客户端（login/validate/heartbeat/report）
│   ├── guard.py        # 四端统一登录校验（require_login）
│   ├── reporter.py     # 审计上报（事件队列/心跳/落盘重试/快照）
│   ├── events.py       # 事件构造 + 权限快照采集
│   ├── guide.py        # 登录指引文案（全端统一）
│   └── login_cmd.py    # login/logout/whoami/ping 命令实现
├── admin/             # 管理服务端（v5.4.0，仅服务器端使用）
│   ├── server.py       # FastAPI 应用 + run_admin 启动入口（含主密码门）
│   ├── master.py       # 管理员主密码校验（内置默认 + env + admin.json）
│   ├── store.py        # SQLite 存储层（users/tokens/sessions/events/logins）
│   ├── live.py         # 内存实时注册表（在线用户/运行中会话）
│   ├── auth_api.py     # 客户端认证 API（login/validate/heartbeat/logout）
│   ├── report_api.py   # 审计上报接收（batch/snapshot）
│   ├── admin_api.py    # 管理页面 API（用户/会话/事件/设置/导出）
│   └── static/         # 管理页面 SPA（零构建，?v= 缓存参数）
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
```

## 变更记录

### v5.4.1

**修复：管理页面修改主密码不生效（用户报告）+ 服务设置全项排查**：

- **主密码"显式修改优先"语义**：此前只要启动配置里有 `CBHCLI_ADMIN_MASTER_KEY`（crontab/systemd），管理页面改密码**永远不生效**（新密码 401、旧密码 200，重启也一样）——现改为：**已修改的主密码（admin.json `key_source=explicit`）优先于环境变量，改后立即生效**（管理页面/新会话登录用新密码，重启后依然如此）；改密码后吊销其它管理会话；设置页/status 显示实际生效的「主密码校验源」
- **主密码修改自动化（用户反馈）**：改密码后用新密码登录即可——运行中的服务自动同步进程内环境变量，**外部启动配置无需修改**（旧值仅打印提示、不阻塞启动，仍以新主密码为准）；`--set-password` 修改密码必须验证当前密码（无跳过校验的通道）
- **服务设置服务端校验**：`PUT /admin/api/settings` 此前无任何校验（可写入 `grace=-5`、`snap="abc"` 等脏值导致客户端异常）→ 新增类型+范围校验（非法值 400），历史脏值读取时回落默认
- **`auto_renew` 死设置补全**：此前下发但无法配置（不在白名单、无界面入口）→ 设置页新增开关（关闭后凭据到期需人工续期）
- **`report_level=summary` 落地**：此前服务端下发但客户端从不消费 → 客户端 reporter 在 summary 级别跳过实时流式内容（`stream_chunk`），仅保留结构化审计事件
- **用户续期"永久"修复**：`update_user` 通用 `None 跳过` 逻辑吞掉永久值 → 本次续期永久、下次登录又恢复旧天数；现可正确写回用户记录（含续期弹窗对永久用户默认显示"永久"）
- **其它**：管理登录限流只计失败（成功登录清空计数，改密码后重新登录不被误伤）；同数据目录多实例退出不再互相删除 `server.json`；快照上限脏值防御；**审计日志「事件日志」时间列改为"日期 时间"格式**（与登录记录一致）
- **详见**：`docs/v5.4.1更新日志.md`

### v5.4.0

**用户认证与审计系统（内网多用户）+ cbhcli-admin 管理服务**：

- **四端统一认证**：CLI / Web / JupyterLab 插件 / exec 共享 `~/.cbhcli/auth.json` 设备级凭据，**登录一次四端通行**；未登录时 CLI 允许进入但发消息拦截（`/login` 就地登录，无需重启）、Web 登录界面（任意 401 自动切回）、exec 退出码 2、Jupyter 全 API 401 + 提示横幅
- **新包 `cbhcli_pkg/auth/`**：凭据存储（0600）/ guard 统一拦截（过期必须在线确认——管理员续期后无感恢复；软在线 + strict_online 严格模式）/ reporter 审计上报（事件队列 2s 批报 + 60s 心跳 + 落盘重试 + atexit 同步 flush）/ 登录指引文案（全端统一）
- **新包 `cbhcli_pkg/admin/`**（管理服务端，端口 **28888**）：主密码启动门（内置默认，env / admin.json 可覆盖；连错 5 次退出码 2）+ 管理页面 7 视图（登录 / 仪表盘 / 用户管理 / 会话管理 / 实时监控 / 审计日志 / 设置）+ 用户管理（创建（**密钥随时查看/复制**）/ 续期 / 重置密钥 / 禁用 / 删除；有效期 7/30/90/365 天 / 自定义 / **永久**）+ 会话浏览（**思考 → 文字 → 工具调用**顺序 + 运行中实时画面）+ 实时监控（**全程可见** + 点击卡片进入 WebSocket 实时交互画面）+ 审计日志（含 CSV 导出）+ 服务设置；`cbhcli-admin` 独立入口 + `status` 自检
- **管理页面实时化（v5.4.0 迭代）**：新增 `/admin/ws` WebSocket 实时推送（客户端流式内容聚合上报 `stream_chunk`；**迭代 2：≥120 字符或 0.18s 一批** + 前端增量渲染——真实链路实测中位间隔 218ms，接近连续流式）；**多进程心跳按实例隔离**（修复 Web/Jupyter 常驻进程空闲心跳覆盖 CLI 运行中会话导致的看板闪烁/消失）；运行中判定 TTL 240s；**会话列表实时更新**（WS 触发 + 轮询兜底，新会话/运行中状态秒级上屏）；密钥**眼睛两态**（浅色/深色，点击后完整显示）；**Web 界面切换 agent 提速**（列表刷新并行 + 恢复会话快速候选，实测 185ms）
- **三段式审计上报**：状态流（60s 心跳，"运行中"秒级上屏）/ 事件流（工具调用 / 权限拦截 / 模式切换 / 压缩 / fallback，2s 批报）/ 会话快照（每轮去重，**本地删除后管理端仍保留**）；断网软在线 + 落盘补传（eid 幂等去重）
- **新命令**：`cbhcli login / logout / whoami / ping / admin`、`cbhcli-admin [status]`；界面内 `/login /logout /whoami /ping`
- **其余**：`core/tracer.py` 审计镜像（工具级事件全端统一）；`session_history.build_session_data()` 提取重构（`exec --no-save` 不可绕过审计快照）；`pyproject.toml` 新增 `cbhcli-admin` 入口与 admin 静态资源打包；CLI 方框类提示全部改为动态宽度对齐（字素簇感知，框内禁用 emoji）
- **详见**：`docs/v5.4.0更新日志.md` 与 README「用户认证与审计系统」章节

### v5.3.3

**会话加载全面提速 + CLI 恢复会话回显历史**：

- **Web 加载提速（MCP 连接进程级缓存）**：新建会话 / 恢复历史会话 / 切换 Agent 时不再重复 MCP 握手（每次 0.8~1.0s 的网络往返），改为复用进程内已建立的连接与工具定义 —— 实测会话创建 1656ms → 3ms。配置内容变化（增删服务器/改 header/启停工具）自动失效重连，`/mcp refresh` 仍强制刷新；服务器短暂不可用时记负缓存（120s 内不重复阻塞），工具调用遇会话失效会重新握手重试一次
- **服务启动后台预热**：Web 服务启动后立即在后台线程初始化向量库并预连接各 Agent 的 MCP 服务器，用户第一次操作即命中缓存
- **会话列表零解析（元数据缓存）**：侧边栏/CLI `/resume` 列会话不再为每个历史文件解析整份 `messages`（500 个文件实测 445ms → 9ms），改为只读文件头部的元数据字段 + 按 mtime/size 缓存
- **`usage_stats` 签名缓存**：大会话（700+ 条消息）的 token 统计从每次 100ms+ 降为一次计算后直接复用（内容变化自动失效）
- **CLI `/resume` 恢复后回显历史对话**：对齐 Web 端 —— 用户消息灰底高亮、AI 回复按 Markdown 渲染（代码块/表格/公式）、工具调用折叠为一行摘要；默认回显最近 20 条、单条超长截断，完整上下文照常恢复
- **修复所有交互式命令报 `is_done` 错误**（`core/resize_fix.py`，v5.3.2 第四轮引入）：`Renderer.render` 的 resize 包装签名漏了 `is_done` 参数，导致 `/resume` 菜单、`/undo-compress`、`/mcp add` 等所有走交互输入的命令都"命令执行失败"；现改为 `*args/**kwargs` 原样透传（`_report_wrapper`、`_debounced_on_resize` 一并加固）

### v5.3.2

MCP 接入体验增强（带认证的远程 MCP 服务，如阿里云百炼）：

- **`/mcp add` 支持引号包裹含空白的 header 值**：`Authorization="Bearer sk-xxx"` —— 旧版按空白拆分会把 token 丢掉（只剩 `Bearer`），现支持单/双引号包裹（引号未配对时回退旧行为，兼容不变）
- **名称可省略**：`/mcp add <URL> [header...]` 自动从 URL 推导服务器名（`.../TextGenerateImage/mcp` → `TextGenerateImage`；自动跳过 mcp/sse/v1 等通用段）
- **header 值支持 `${环境变量}` / `$环境变量` 占位符**：连接时展开，CLI / Web / 手写 mcp.json 全端生效；配置可只存占位符（API Key 不落明文）；变量缺失时连接失败信息会明确提示缺哪个变量
- 连接层 headers 改为副本展开：展开的明文不回写配置（顺带不再把 `Accept`/`Content-Type` 写进 mcp.json）
- 帮助文案与 README 中会丢 token 的错误认证示例已修正

**同版本迭代追加：CLI 终端 resize 输入框残留 ❯ 彻底修复（`cbhcli_pkg/core/resize_fix.py` 第四轮重写）**

- **现象**：终端窗口拖小后输入框出现"一串 ❯"（每次改窗口多一个旧 `❯`，旧版实测 2→3→4→5 递增）
- **根因**：终端 resize 后 reflow（长行重折 + 滚屏）时，**xterm.js 类终端（Jupyter 终端、VS Code 终端）光标停在旧行不跟随内容**，而旧实现直接用 CPR 光标行推算"旧区域顶" → 每 resize 残留一行
- **修复**：清除起点改用双路估算 —— ① CPR 反推（光标跟随内容时准确，**偏移按新宽度用干渲染重算**，修复多行输入折行数变化导致的 ±N 行偏差）；② 区域顶估算 `上次区域顶 − 区域自身 reflow 增长`（识别"光标根本没动"的陈旧光标终端，宁可多清也不残留）。reflow 增长按"同逻辑行拼接 + 只统计真会写进终端的格子"估算，间隙先吸收增量
- **回归**：真实 cbhcli × 终端仿真台（xterm.js）下，`❯` 恒为 1（旧版逐次累积），banner/历史内容不被擦除，输入/提交/多行输入均正常；`tests/terminal_resize/` 新增 scene14（细粒度拖拽）、scene15（内容完整性编号校验）。**用户实测确认修复 ✓**（重启 cbhcli 后拖小窗口，输入框不再残留一串 `❯`）

### v5.3.1（建模护栏补强，同版本内迭代）

Harness（确定性建模护栏）假阴性防护与检测能力补强：

- **A0 预填充检测（问题1修复）**：`check_data` 须传原始含 NaN 数据 + nan 参数——若传已 `fillna` 的数据，数据中无缺失信息、A1/A2 填充值安全性检查全部失效（假阴性），现显式提示 A0 而非静默"全部通过"
- **D0/E0 输入结构校验（问题2修复）**：`check_overfit`/`check_stability` 只认真实工具链产出文件（report 的 `confusion_matrix_*.xlsx`、get_psi 的 `psi_single_rpt_*.xlsx`）；手工模拟/缺标准列（`type[train/test]+auc/ks`、`psi`）的文件会触发 D0/E0 结构警告且 D1/E1 不执行（E0 能识别 psi_avg 等候选列并提示改名）
- **C5 三层泄漏检测（问题3修复）**：完美泄漏特征（如 target×10）经 adj_bin 分箱后 WOE/IV 被置 0"洗白"（实测报告 IV=0、仅看 iv_data 完全漏报）——① 从 woe_data 分箱明细重算**修正 IV**（单侧箱 ±0.5 连续化，结果注入 `corrected_iv` 会话变量）；② **原始数据级点二列相关**（|r|>0.9）终极兜底（覆盖泄漏特征被合并成单箱、分箱明细失效的极端场景）；③ `comp_woe_iv` 与 `check_leakage` 内置①+②自动报警。修正 IV 启发式报警后仍需人工审查分箱明细（bad_rate 0%/100% 箱）最终确认
- **data_to_woe 支持 output_csv（问题4修复）**：可指定 WOE 转换结果另存路径（下游 cbhpacks_binary_model 训练直接作 train_csv），默认仍写 `output_path/woe_transformed_data.csv`
- **get_psi 月份预校验（问题5修复）**：cmp_mth/base_mth 不在数据月份列中时明确报错并列出可用月份（替代裸 `KeyError: 202408`），并建议改用 `psi_mth_avg`（用全量数据或自动遍历各月）
- **B1 文案修正（问题6）**：单好/单坏箱警告改为分类计数（"N 个坏样本=0，M 个好样本=0"），消除"无坏样本/好样本"歧义
- **get_psi/psi_mth_avg 状态恢复**：底层库 get_psi 循环中途会把实例 df 替换为单月子集且异常路径不恢复，污染会话缓存实例（后续 comp_woe_iv 复用会静默用单月数据分箱）——工具层 try/finally 恒恢复实例状态
- **report 混淆矩阵 all 行 F1 修正**：底层库 all 行 F1 误用 test 行的 f1（应为 f1_all），修正后 confusion_matrix_*.xlsx 全表正确（该文件是 check_overfit 的输入）
- **lr_fit/svm_fit NaN 友好预检**：训练特征含 NaN 时返回友好报错（列出缺失列+三种解决方案），替代 sklearn 的 `Input X contains NaN` 晦涩异常

### v5.3.1

- Harness 建模护栏：A（缺失填充）/B（分箱质量）/C（数据穿越）/D（过拟合）/E（稳定性）五组确定性检查，内置到分箱/训练工具并支持 cbhpacks_harness 显式调用
- 内置 cbhpacks 数据科学 Agent（评分卡/风控建模方向）
- data_to_woe 分箱参数自动继承 + 传入参数不一致 C2 警告；get_psi 缺失值对比修复；cat_bin 跳过单调性检查等

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
