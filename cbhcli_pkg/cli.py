"""CLI入口和参数解析"""
import argparse
import sys
from cbhcli_pkg import __version__


def print_help():
    """打印帮助信息"""
    print(f"CBHCLI v{__version__} - AI驱动的终端助手")
    print("支持多Agent管理、工具调用、知识库、技能系统和会话管理\n")
    print("用法: cbhcli [选项]")
    print("\n子命令:")
    print("  web              启动 Web 管理界面 (cbhcli web -p 18888)")
    print("  exec             一次性非交互执行任务（headless 模式，用于脚本/CI/管道）")
    print("                     cbhcli exec \"任务...\"              # 默认静音，stdout 只含结果")
    print("                     echo \"任务...\" | cbhcli exec       # stdin 管道")
    print("                     cbhcli exec -v \"任务...\"           # -v 显示过程输出(stderr)")
    print("                     cbhcli exec --agent xxx --model xxx --mode yolo \"任务...\"")
    print("                     cbhcli exec -c \"继续上次任务\"    # 续接最近会话")
    print("                     cbhcli exec --output-format json \"任务...\" | jq .result")
    print("\n选项:")
    print("  --version, -v    显示版本信息")
    print("  --help, -h       显示此帮助消息")
    print("\n斜杠命令 (在应用内使用):")
    print("")
    print("  Agent 管理:")
    print("    /agent add <name>     创建新Agent")
    print("    /agent list           列出所有Agent")
    print("    /agent use [name]     切换Agent")
    print("    /agent rm [name]      删除Agent")
    print("")
    print("  模型配置:")
    print("    /model add            添加新模型")
    print("    /model list           列出所有模型")
    print("    /model use [name]     使用指定模型")
    print("    /model rm [name]      删除模型")
    print("    /model info           查看当前模型信息")
    print("    /model config         修改模型参数")
    print("    /model embedding      配置嵌入模型(向量搜索)")
    print("    /model rerank         配置重排序模型(搜索优化)")
    print("")
    print("  会话管理:")
    print("    /reset 或 /new        重置/新建会话")
    print("    /comp [指令]          压缩上下文（可带保留/丢弃指令）")
    print("    /undo-compress [编号] 撤销最近一次上下文压缩")
    print("    /ctx                  查看上下文使用")
    print("")
    print("  权限与安全 (Harness):")
    print("    /mode [模式]          权限模式: readonly/standard/auto/yolo")
    print("    /permissions [list|add|rm]  权限规则管理")
    print("    /hooks [list|reload|test]   生命周期钩子管理")
    print("    /undo [ID|list]       回滚 write/edit 的文件修改")
    print("")
    print("  知识库管理:")
    print("    /kb add <file>        添加文件到知识库")
    print("    /kb list              列出知识库文件")
    print("    /kb rm [file]         从知识库删除文件")
    print("    /kb reindex           重新索引整个知识库")
    print("    /kb status            查看知识库状态")
    print("")
    print("  向量索引:")
    print("    /embedding index      索引 Agent 工作空间到向量数据库")
    print("    /embedding status     查看索引状态")
    print("    /embedding clear      清除向量索引")
    print("    /embedding reindex    重新索引（清除后重建）")
    print("")
    print("  技能管理:")
    print("    /skills list          列出所有已注册技能")
    print("    /skills add [name]    创建技能")
    print("    /skills use [name]    选择激活技能（支持多选）")
    print("    /skills off [name]    取消激活技能")
    print("    /skills rm [name]     删除技能")
    print("")
    print("  MCP 管理:")
    print("    /mcp add <名> <URL>   添加 MCP 服务器")
    print("    /mcp list             列出 MCP 服务器")
    print("    /mcp tools [名]       查看服务器工具")
    print("    /mcp rm [名]          移除 MCP 服务器")
    print("    /mcp refresh [名]     刷新服务器工具")
    print("    /mcp on [服务器] [工具]   启用工具")
    print("    /mcp off [服务器] [工具]  禁用工具")
    print("")
    print("  工具管理:")
    print("    /tools list           查看工具开关状态")
    print("    /tools on             开启工具（交互式多选）")
    print("    /tools off            关闭工具（交互式多选）")
    print("")
    print("  备用模型管理:")
    print("    /fallback list                 查看备用模型配置")
    print("    /fallback add [main|vision] <模型名>  添加备用模型")
    print("    /fallback rm [main|vision] <模型名>   移除备用模型")
    print("    /fallback reorder [main|vision]       重新排序备用模型")
    print("    /fallback clear [main|vision]        清空备用模型列表")
    print("")
    print("  Agent 链条:")
    print("    /chain list             列出所有链条")
    print("    /chain add              创建新链条（交互式引导）")
    print("    /chain use              激活链条（从列表选择）")
    print("    /chain off              取消链条绑定")
    print("    /chain show             查看链条详情（从列表选择）")
    print("    /chain rm               删除链条（从列表选择）")
    print("    /chain config           编辑链条配置（从列表选择）")
    print("    /chain rename           重命名链条（从列表选择）")
    print("")
    print("  其他:")
    print("    /help [command]       显示帮助信息")
    print("    quit                  退出程序")
    print("")
    print("通用工具 (16个, 默认开启):")
    print("  terminal, read, write, edit, grep, glob, python")
    print("  Todo, ask_user, memory_search, knowledge_base")
    print("  skills_create, delegate_task, image, process, kill_process")
    print("")
    print("cbhpacks 数据科学工具 (14个, 默认关闭, /tools on 开启):")
    print("  cbhpacks_bins_model      分箱WOE/IV/PSI计算")
    print("  cbhpacks_binary_model    二分类模型训练评估")
    print("  cbhpacks_uns_model       无监督学习PCA/聚类")
    print("  cbhpacks_linear_model    线性回归/工具变量")
    print("  cbhpacks_cols_select     特征筛选(10种方法)")
    print("  cbhpacks_cols_select_js  递归特征筛选")
    print("  cbhpacks_cols_encode     特征编码(7种方法)")
    print("  cbhpacks_cols_operate    列操作(炸裂/转置/分词)")
    print("  cbhpacks_desc_df         数据集描述统计")
    print("  cbhpacks_desc_col        单变量分析/异常值检测")
    print("  cbhpacks_con_sql         数据库连接SQL执行")
    print("  cbhpacks_con_linux       Linux SSH连接命令")
    print("  cbhpacks_get_random_data 生成随机测试数据")
    print("  cbhpacks_harness         建模护栏校验(穿越/过拟合/分箱质量)")
    print("")
    print("快捷键:")
    print("  Ctrl+R          切换工具显示详细/简洁模式")
    print("  Shift+Tab       循环切换权限模式（YOLO 需再按一次确认）")


def main():
    """主入口函数"""
    # exec 子命令（一次性非交互执行）：基于原始 argv 直接分发，
    # 绕过顶层 parser 对 -h/-v 的抢先消费，保证 cbhcli exec -h 正确显示 exec 帮助
    argv = sys.argv[1:]
    if argv and argv[0] == 'exec':
        _run_exec(argv[1:])
        return

    parser = argparse.ArgumentParser(
        prog='cbhcli',
        description='AI驱动的终端助手',
        add_help=False
    )

    parser.add_argument('--version', '-v', action='store_true',
                       help='显示版本信息')
    parser.add_argument('--help', '-h', action='store_true',
                       help='显示帮助消息')

    args, unknown_args = parser.parse_known_args()

    if args.version:
        print(f"cbhcli version {__version__}")
        return

    if args.help:
        print_help()
        return
    
    # 检查是否是 web 子命令
    if unknown_args and unknown_args[0] == 'web':
        _run_web(unknown_args[1:])
        return
    
    if unknown_args:
        print(f"未识别的参数: {' '.join(unknown_args)}")
        print_help()
        sys.exit(1)
    
    # 启动主应用
    from cbhcli_pkg.core.app import CBHCLIApp
    
    try:
        app = CBHCLIApp()
        app.run()
    except KeyboardInterrupt:
        print("\n\n👋 再见!")
    except Exception as e:
        print(f"\n❌ 错误: {str(e)}")
        sys.exit(1)


def _run_exec(args: list):
    """一次性非交互执行任务（headless 模式）

    对标 claude -p / codex exec：任务 -> ReAct 循环 -> 结果到 stdout -> 退出。
    退出码：0=成功 | 1=执行错误 | 2=用法错误或权限拒绝 | 130=用户中断
    """
    parser = argparse.ArgumentParser(
        prog='cbhcli exec',
        description='一次性非交互执行任务（headless 模式，用于脚本/CI/管道集成）',
    )
    parser.add_argument('prompt', nargs='?', default=None,
                        help='任务描述；缺省且 stdin 非 TTY 时自动读 stdin，"-" 显式读 stdin')
    parser.add_argument('--agent', default=None,
                        help='使用指定 Agent（默认上次激活的 Agent）')
    parser.add_argument('--model', default=None,
                        help='本次临时使用指定模型（不写入持久化配置）')
    parser.add_argument('--mode', choices=['readonly', 'standard', 'auto', 'yolo'],
                        default='yolo', help='权限模式（默认: yolo 零确认）')
    parser.add_argument('--max-turns', type=int, default=999,
                        help='ReAct 循环最大轮次保护（默认: 999）')
    parser.add_argument('--output-format', choices=['text', 'json'],
                        default='text', help='text=只输出最终回答；json=输出结构化结果')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='显示过程输出（工具执行/思考过程实时走 stderr）；'
                             '默认静音，stdout 只含最终结果（管道友好）')
    parser.add_argument('--continue', '-c', dest='continue_session',
                        action='store_true',
                        help='续接该 Agent 最近一次历史会话后执行')
    parser.add_argument('--resume', dest='resume_session', default=None,
                        metavar='SESSION_ID',
                        help='恢复指定历史会话后执行（会话ID或文件名）')
    parser.add_argument('--no-save', action='store_true',
                        help='本次会话不写入 history')
    parser.add_argument('--cwd', default=None,
                        help='启动前切换到指定工作目录')

    ns = parser.parse_args(args)

    from cbhcli_pkg.core.exec_runner import run_exec
    sys.exit(run_exec(ns))


def _run_web(args: list):
    """启动 Web 服务"""
    port = 18888
    host = "0.0.0.0"

    # 解析 -p / --port 参数
    i = 0
    while i < len(args):
        if args[i] in ("-p", "--port") and i + 1 < len(args):
            try:
                port = int(args[i + 1])
            except ValueError:
                print(f"无效的端口号: {args[i + 1]}")
                sys.exit(1)
            i += 2
        elif args[i] in ("--host",) and i + 1 < len(args):
            host = args[i + 1]
            i += 2
        elif args[i] in ("-h", "--help"):
            print("用法: cbhcli web [选项]")
            print("\n选项:")
            print(f"  -p, --port PORT   指定端口号 (默认: 18888)")
            print(f"  --host HOST       指定监听地址 (默认: 0.0.0.0)")
            return
        else:
            print(f"未识别的参数: {args[i]}")
            sys.exit(1)

    try:
        from cbhcli_pkg.web.server import run_server
        run_server(port=port, host=host)
    except ImportError as e:
        print(f"启动 Web 服务需要安装额外依赖:")
        print(f"  pip install fastapi uvicorn")
        print(f"\n错误详情: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nWeb 服务已停止")
    except Exception as e:
        print(f"\n❌ 启动失败: {str(e)}")
        sys.exit(1)


# 当直接运行此模块时调用 main
if __name__ == "__main__":
    main()
