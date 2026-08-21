"""cbhcli exec - 一次性非交互执行入口（headless 模式）

对标 claude -p / codex exec：终端一次性下发任务 -> ReAct 循环自动完成 ->
结果输出到 stdout -> 进程退出。用于 shell 脚本、CI/CD、管道集成。

设计要点（见 cbhcli_exec_headless_用户提示词.md）：
- 复用 CBHCLIApp 全套组件与钩子链，跳过交互主循环 run()
- fd 级 stdout 重定向：默认静音（-> devnull，stdout 只放最终结果，管道友好）；
  --verbose 时过程输出走 stderr 实时可见（verbose+stdout为TTY时跳过最终回答
  的重复打印，防终端同屏输出两遍）
- 运行时补丁（不改核心文件本体，仅本进程生效）：
  * AIHandler.process_request 包装捕获最终回复（_handle_ai_request 不透传返回值）
  * ThinkingDisplay 禁用原地重绘（ANSI 控制序列不进管道/日志）
  * 非 yolo 模式下 execute_with_display 预检 ASK -> 一律拒绝（禁止终端确认等待）
  * ai_handler.MAX_TOOL_ROUNDS 模块属性按 --max-turns 覆盖
- 交互工具（ask_user）从注册表增量禁用：LLM 不可见 + 执行层拒绝双保险

退出码：0=成功 | 1=执行错误 | 2=用法错误或权限拒绝 | 130=用户中断
"""

import json
import os
import sys

# --- 退出码约定 ---
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_DENIED = 2
EXIT_INTERRUPTED = 130

# headless 下禁用的交互类工具（增量加入 disabled_tools，禁止整体覆盖，
# 避免误动 Agent 配置里用户手动禁用的其他工具）
HEADLESS_DISABLED_TOOLS = ["ask_user"]

# process_request 达到轮次上限时的固定返回文案（ai_handler.py）
_MAX_TURNS_TEXT = "达到最大工具调用轮数"


class ExecError(Exception):
    """exec 模式环境/参数错误（退出码 1）"""


# ======================================================================
#  fd 级 stdout 重定向
# ======================================================================

def _redirect_stdout(to_stderr: bool) -> int:
    """fd 级重定向 stdout：to_stderr=True -> 跟随 stderr；False -> devnull

    覆盖一切经 fd1 的输出（print / rich Console / 子进程继承），
    保证 stdout 管道里只有最终结果。

    Returns:
        原 fd1 的副本，供 _restore_stdout 恢复
    """
    sys.stdout.flush()
    sys.stderr.flush()
    saved = os.dup(1)
    if to_stderr:
        os.dup2(2, 1)
    else:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.close(devnull)
    return saved


def _restore_stdout(saved: int) -> None:
    """恢复 stdout 到原 fd"""
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved, 1)
        os.close(saved)
    except OSError:
        pass


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


# ======================================================================
#  应用构建与补丁安装
# ======================================================================

def _build_app(args, state: dict):
    """构造 CBHCLIApp 并完成 agent/model/mode/补丁/会话恢复的全部准备"""
    from cbhcli_pkg.core.app import CBHCLIApp

    app = CBHCLIApp()

    # --agent：加载指定 Agent（不存在则报错并列出可用项）
    if args.agent and args.agent != app.current_agent_name:
        if not app._load_agent(args.agent):
            names = ", ".join(
                getattr(c, "name", "?") for c in app.agent_manager.list_agents()
            )
            raise ExecError(f"Agent '{args.agent}' 不存在。可用 Agent: {names}")

    # 模型可用性校验（headless 没有斜杠命令，报错需给出交互式解决指引）
    if not app.llm_client:
        raise ExecError(
            "当前 Agent 未配置可用模型。请先在交互模式 (cbhcli) 中执行 "
            "/model add 配置模型，或通过 Web 界面 (cbhcli web) 配置后再使用 exec。")

    # --model：本次临时切换（switch_model 仅替换进程内组件，不写持久化配置）
    if args.model:
        model_config = app.global_config.get_model(args.model)
        if not model_config:
            names = ", ".join(
                m.get("name", "?") for m in app.global_config.get_models()
            )
            raise ExecError(f"模型 '{args.model}' 不存在。可用模型: {names}")
        app.switch_model(model_config)

    # 权限模式（仅进程内生效，不持久化）
    if not app.set_permission_mode(args.mode):
        raise ExecError(f"无效的权限模式: {args.mode}")

    _install_patches(app, state, args)
    _restore_history(app, args)

    # --no-save：同时关闭每轮自动保存（v5.2.6 起 _handle_ai_request 的
    # finally 会每轮自动落盘，--no-save 语义需一并覆盖，保证完全不留痕）
    if getattr(args, "no_save", False):
        app.autosave_history = False

    return app


def _install_patches(app, state: dict, args) -> None:
    """安装 headless 运行时补丁（全部为进程内 monkey-patch，不动文件本体）"""
    import cbhcli_pkg.core.ai_handler as _ah
    from cbhcli_pkg.core.thinking_display import ThinkingDisplay
    from cbhcli_pkg.tools.registry import ToolResult
    from cbhcli_pkg.core import permissions as _perm

    executor = app.tool_executor

    # 1) ReAct 轮次上限（process_request 循环引用模块级名字）
    _ah.MAX_TOOL_ROUNDS = max(1, args.max_turns)

    # 2) 捕获 process_request 最终回复（_handle_ai_request 丢弃返回值）
    _orig_pr = _ah.AIHandler.process_request

    def _capturing_pr(self, user_input):
        result = _orig_pr(self, user_input)
        state["result"] = result
        return result

    _ah.AIHandler.process_request = _capturing_pr

    # 3) 思考内容滚动显示禁用（headless 不做 ANSI 原地重绘）
    _orig_td_init = ThinkingDisplay.__init__

    def _quiet_td_init(td_self, *a, **k):
        _orig_td_init(td_self, *a, **k)
        td_self.enabled = False

    ThinkingDisplay.__init__ = _quiet_td_init

    # 4) spinner 等动画关闭（Spinner 自带 isatty 检测，此处双保险）
    executor.animations_enabled = False

    # 5) 工具确认策略：禁止任何终端确认交互
    if args.mode == "yolo":
        # 默认 yolo：零确认放行（Web 端同款机制）
        executor.no_more_confirmations = True
    else:
        # 显式 standard/auto/readonly：ASK 类确认一律拒绝，
        # 预检在权限引擎评估后、原执行流程前，返回带明确指引的错误
        _orig_ewd = executor.execute_with_display

        def _deny_ask_ewd(tool_name, arguments, tool_call_id=None):
            action, _rule = app.permission_engine.check(tool_name, arguments or {})
            if action == _perm.ASK:
                state["perm_denied"] = True
                result = ToolResult(
                    success=False,
                    output="",
                    error=("非交互模式(exec)下该操作需要人工确认，已被拒绝。"
                           "请改用 --mode yolo 运行，或在交互模式下执行。"))
                if executor._on_tool_execute:
                    executor._on_tool_execute(
                        tool_name, arguments, result, tool_call_id)
                return result
            return _orig_ewd(tool_name, arguments, tool_call_id)

        executor.execute_with_display = _deny_ask_ewd

    # 6) 权限拒绝事件统计（readonly deny / 红线 deny / 钩子拦截也计入）
    def _on_tool_exec(tool_name, arguments, result, tool_call_id=None):
        err = getattr(result, "error", "") or ""
        if ("权限规则禁止" in err or "用户取消" in err
                or "钩子拦截" in err or "非交互模式" in err):
            state["perm_denied"] = True

    executor.on_tool_execute(_on_tool_exec)

    # 7) 交互类工具增量禁用（get_openai_tools 不返回 -> LLM 不可见；
    #    execute 层同时拒绝，双保险）
    disabled = set(getattr(app.tool_registry, "_disabled_tools", set()))
    disabled.update(HEADLESS_DISABLED_TOOLS)
    app.tool_registry.set_disabled_tools(list(disabled))


def _restore_history(app, args) -> None:
    """--continue / --resume 恢复历史会话（原地恢复，v5.2.3 摘要保留语义）"""
    if not (getattr(args, "continue_session", False)
            or getattr(args, "resume_session", None)):
        return

    sessions = app.session_history.list_sessions(limit=50)
    target = None
    if args.resume_session:
        sid = args.resume_session
        for s in sessions:
            if (s.get("id") == sid or s.get("filename") == sid
                    or s.get("filename", "").startswith(sid)):
                target = s
                break
        if not target:
            raise ExecError(
                f"找不到会话: {sid}（可在交互模式 /history 查看会话列表）")
    else:
        if not sessions:
            raise ExecError("没有可续接的历史会话")
        target = sessions[0]

    messages = app.session_history.load_session(target["filename"])
    if not messages:
        raise ExecError(f"加载会话失败: {target['filename']}")

    from cbhcli_pkg.context.compressor import SUMMARY_MARKER

    # 原地恢复：保留当前首条主系统提示（含最新 skills/tools/memory），
    # 跳过旧主提示、保留"[历史对话摘要]"（对齐 /resume，v5.2.3）
    if app.session.messages and app.session.messages[0].role == "system":
        app.session.messages = [app.session.messages[0]]
    else:
        app.session.messages = []
    app.session.tool_call_count = 0

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content") or ""
        if role == "system" and not content.startswith(SUMMARY_MARKER):
            continue
        app.session.add_message(
            role=role,
            content=content,
            token_count=app.token_counter.count_tokens(content),
            metadata=msg,
            tool_call_id=msg.get("tool_call_id"),
            tool_calls=msg.get("tool_calls"),
            reasoning_content=msg.get("reasoning_content"),
        )

    # 沿用历史会话 ID：--resume/--continue 多轮串联后文件 id 一致
    if target.get("id"):
        app.session.id = target["id"]


# ======================================================================
#  统计与输出
# ======================================================================

def _msg_stats(session) -> tuple:
    """统计会话消息：(带工具调用的assistant条数, prompt类token, completion类token)"""
    rounds = pt = ct = 0
    for m in session.messages:
        tok = m.token_count or 0
        if m.role == "assistant":
            ct += tok
            if m.tool_calls:
                rounds += 1
        else:
            pt += tok
    return rounds, pt, ct


def _save_session(app) -> None:
    """保存会话到 history（供 --continue/-c/--resume 续接）"""
    try:
        if app.session and app.session_history \
                and len(app.session.messages) > 1:
            app.session_history.save_session(
                app.session.get_context_messages(), app.session.id)
    except Exception:
        pass


def _exit_reason_code(state: dict, result: str) -> tuple:
    """根据运行状态判定 (exit_reason, 退出码)"""
    if state.get("interrupted"):
        return "interrupted", EXIT_INTERRUPTED
    if result == _MAX_TURNS_TEXT:
        return "max_turns", EXIT_ERROR
    if "死循环熔断" in (result or ""):
        return "loop_break", EXIT_ERROR
    if state.get("perm_denied"):
        return "permission_denied", EXIT_DENIED
    return "done", EXIT_OK


# ======================================================================
#  主入口
# ======================================================================

def run_exec(args) -> int:
    """执行一次性任务，返回进程退出码

    Args:
        args: argparse Namespace（cli.py._run_exec 解析）
    """
    # --cwd：启动前切换工作目录
    if getattr(args, "cwd", None):
        try:
            os.chdir(os.path.expanduser(args.cwd))
        except OSError as e:
            _eprint(f"❌ 无法切换到目录 {args.cwd}: {e}")
            return EXIT_ERROR

    # prompt 来源：位置参数 / "-"（显式 stdin）/ stdin 非 TTY 时自动读取
    prompt = args.prompt
    if prompt == "-":
        prompt = "" if sys.stdin.isatty() else sys.stdin.read().strip()
    elif prompt is None and not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    if not prompt:
        _eprint("用法: cbhcli exec [选项] PROMPT（或通过 stdin 管道传入）")
        _eprint("运行 cbhcli exec -h 查看完整帮助")
        return EXIT_DENIED

    state = {"result": None, "perm_denied": False, "interrupted": False}
    app = None
    error = None

    # 过程输出重定向：默认静音（-> devnull，headless 本意）；--verbose -> stderr 实时可见
    saved_fd = _redirect_stdout(to_stderr=bool(getattr(args, "verbose", False)))
    try:
        app = _build_app(args, state)
        state["baseline"] = _msg_stats(app.session)
        app._handle_ai_request(prompt)  # 复用压缩检查 + UserPromptSubmit/Stop 钩子链
    except KeyboardInterrupt:
        state["interrupted"] = True
    except ExecError as e:
        error = str(e)
    except Exception as e:
        error = f"执行失败: {e}"
    finally:
        if app is not None and not args.no_save:
            _save_session(app)
        _restore_stdout(saved_fd)

    if error is not None:
        _eprint(f"❌ {error}")
        return EXIT_ERROR

    result = state.get("result") or ""
    reason, code = _exit_reason_code(state, result)

    if args.output_format == "json":
        cur = _msg_stats(app.session)
        base = state.get("baseline") or (0, 0, 0)
        payload = {
            "result": result,
            "session_id": app.session.id,
            "agent": app.current_agent_name or "main",
            "model": getattr(app.llm_client, "model_name", ""),
            "rounds": max(1, cur[0] - base[0] + 1),
            "usage": {
                "prompt_tokens": max(0, cur[1] - base[1]),
                "completion_tokens": max(0, cur[2] - base[2]),
            },
            "exit_reason": reason,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if reason == "interrupted" and not result:
            _eprint("⏹ 已被用户中断（Ctrl+C）")
        elif result:
            # stdout 是否打印最终回答：
            # - 默认静音 / stdout 是管道（消费者等结果）-> 必须打印（stdout 唯一一次输出）
            # - --verbose 且 stdout 是 TTY -> 跳过：完整回答已经由流式渲染实时
            #   输出到 stderr，终端同屏再打印 result 会重复（v5.2.5 补丁）
            show_on_stdout = not (getattr(args, "verbose", False)
                                  and sys.stdout.isatty())
            if show_on_stdout:
                print(result)

    return code
