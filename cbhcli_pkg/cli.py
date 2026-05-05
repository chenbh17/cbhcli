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
    print("    /model embedding      配置嵌入模型(向量搜索)")
    print("    /model rerank         配置重排序模型(搜索优化)")
    print("")
    print("  会话管理:")
    print("    /reset 或 /new        重置/新建会话")
    print("    /comp                 压缩上下文")
    print("    /ctx                  查看上下文使用")
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
    print("  其他:")
    print("    /help [command]       显示帮助信息")
    print("    quit                  退出程序")
    print("")
    print("可用工具:")
    print("  terminal        执行终端命令")
    print("  read            读取文件内容")
    print("  write           写入文件")
    print("  edit            编辑文件")
    print("  python          执行Python代码（带会话记忆）")
    print("  memory_search   语义搜索历史对话")
    print("  knowledge_base  查询知识库")
    print("  skills_create   创建新技能")
    print("")
    print("快捷键:")
    print("  Ctrl+R          切换工具显示详细/简洁模式")


def main():
    """主入口函数"""
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
