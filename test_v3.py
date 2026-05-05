│ > 重新构建skills系统。1、每个agent的工作空间目录结构改变，工作空间文件夹下不再直接使用skills.md，而是创建一个skills文件夹，skills文件夹下有不同的技能名称文件夹，每个技能文件夹下，分别由  │
│   skills.md文件和script文件夹构成。其中skills.md是该技能的提示词，以及可执行脚本的路径，当用户通过/skills                                                                                  │
│   use主动选择技能时，会将skills.md文件内容加载到agent系统提示词上下文中，script文件夹下存放当前技能的可执行脚本（脚本可以没有，是可选项），供agent用内置工具执行。2、创建skills注册和调用  │
│   机制，扫描每个agnet工作空间下的skills文件夹下的各技能名称，且各技能文件夹下有skills.md和script文件夹即为注册成功。一方面skills可以通过skills-create工具，由agent自己创建，另一方面skill  │
│   s可以通过/skills                                                                                                                                                                         │
│   add命令引导用户创建。3、创建skills-create工具，skills-create工具会根据用户需求，自行创建自己工作空间下skills文件夹下的技能名称文件夹，并在技能名称文件夹下自主编写skills.md提示词及scri  │
│   pt文件夹下的可执行脚本（可以没有执行脚本，根据用户需求判断是否需要编写执行脚本）。4、创建/skills命令，包含/skills list列出skills，/skills add 添加skills，/skills use                    │
│   选择skills，其中skills可以多选，以及skills rm 删除skills。5、同步修改cbhcli -h、命令显示等模块。
#!/usr/bin/env python3
"""
CBHCLI v3.0 测试脚本
演示新功能的使用
"""
import sys
sys.path.insert(0, '.')

from cbhcli_pkg.core.agent import AgentManager
from cbhcli_pkg.core.session import Session, ContextWindow
from cbhcli_pkg.tools.registry import ToolRegistry
from cbhcli_pkg.tools.terminal import TerminalTool
from cbhcli_pkg.tools.file_read import ReadTool
from cbhcli_pkg.tools.file_write import WriteTool
from cbhcli_pkg.tools.file_edit import EditTool
from cbhcli_pkg.context.token_counter import TokenCounter
from pathlib import Path
import tempfile
import os


def test_tools():
    """测试所有工具"""
    print("="*60)
    print("测试工具系统")
    print("="*60)
    
    # 创建工具注册表
    registry = ToolRegistry()
    registry.register(TerminalTool())
    registry.register(ReadTool())
    registry.register(WriteTool())
    registry.register(EditTool())
    
    print(f"\n✅ 已注册工具: {registry.get_available_tools()}")
    
    # 测试WriteTool
    print("\n1. 测试WriteTool...")
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "test.txt")
        
        result = registry.execute("write", file_path=test_file, content="Hello, World!\nLine 2\nLine 3")
        print(f"   结果: {result.success}")
        print(f"   输出: {result.output}")
        
        # 测试ReadTool
        print("\n2. 测试ReadTool...")
        result = registry.execute("read", file_path=test_file)
        print(f"   结果: {result.success}")
        print(f"   输出:\n{result.output}")
        
        # 测试EditTool
        print("\n3. 测试EditTool...")
        result = registry.execute("edit", 
                                 file_path=test_file,
                                 old_str="Hello, World!",
                                 new_str="Hello, CBHCLI!")
        print(f"   结果: {result.success}")
        print(f"   输出: {result.output}")
        
        # 读取修改后的文件
        print("\n4. 验证修改...")
        result = registry.execute("read", file_path=test_file)
        print(f"   输出:\n{result.output}")
        
        # 测试TerminalTool
        print("\n5. 测试TerminalTool...")
        result = registry.execute("terminal", command="echo '终端工具测试成功'")
        print(f"   结果: {result.success}")
        print(f"   输出: {result.output}")


def test_session():
    """测试会话管理"""
    print("\n" + "="*60)
    print("测试会话管理")
    print("="*60)
    
    # 创建会话
    session = Session(agent_name="test-agent")
    print(f"\n✅ 创建会话: {session.id[:8]}...")
    
    # 添加消息
    session.add_message("system", "你是AI助手")
    session.add_message("user", "你好", token_count=5)
    session.add_message("assistant", "你好!有什么可以帮助你的?", token_count=15)
    
    print(f"✅ 添加消息: {len(session.messages)} 条")
    print(f"✅ 总Token数: {session.get_total_tokens()}")
    
    # 测试上下文窗口
    context_window = ContextWindow(model_limit=128000, compression_ratio=0.8)
    context_window.update(session.get_total_tokens())
    print(f"\n{context_window.get_status_text()}")
    print(f"需要压缩: {context_window.needs_compression()}")
    
    # 测试重置
    session.reset()
    print(f"\n✅ 重置会话: {len(session.messages)} 条消息 (保留system)")


def test_agent():
    """测试Agent管理"""
    print("\n" + "="*60)
    print("测试Agent管理")
    print("="*60)
    
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir) / "agents"
        manager = AgentManager(workspace)
        
        # 创建Agent
        print("\n1. 创建Agent...")
        config = manager.create_agent("test-dev", "测试开发Agent")
        print(f"   ✅ 创建成功: {config.name}")
        print(f"   工作空间: {config.workspace_path}")
        
        # 列出Agent
        print("\n2. 列出Agent...")
        agents = manager.list_agents()
        print(f"   ✅ 共 {len(agents)} 个Agent")
        
        # 加载Agent人格
        print("\n3. 加载Agent人格...")
        persona = manager.load_agent_persona("test-dev")
        print(f"   ✅ Skills: {len(persona.skills)} 字符")
        print(f"   ✅ Soul: {len(persona.soul)} 字符")
        print(f"   ✅ Tools: {len(persona.tools_description)} 字符")
        
        # 测试系统提示构建
        print("\n4. 构建系统提示...")
        system_prompt = persona.build_system_prompt("- terminal: 执行命令")
        print(f"   ✅ 系统提示长度: {len(system_prompt)} 字符")
        print(f"\n   预览:\n{system_prompt[:200]}...")


def test_token_counter():
    """测试Token计数"""
    print("\n" + "="*60)
    print("测试Token计数")
    print("="*60)
    
    counter = TokenCounter()
    
    test_cases = [
        "Hello",
        "Hello, World!",
        "你好世界",
        "这是一段中文测试文本",
        "Mixed 中英文混合 text 123",
    ]
    
    print("\nToken计数结果:")
    for text in test_cases:
        tokens = counter.count_tokens(text)
        chars = len(text)
        print(f"  {text[:30]:<35} → {tokens:3d} tokens ({chars:3d} chars)")


if __name__ == "__main__":
    print("\n🧪 CBHCLI v3.0 功能测试\n")
    
    try:
        test_tools()
        test_session()
        test_agent()
        test_token_counter()
        
        print("\n" + "="*60)
        print("🎉 所有测试通过!")
        print("="*60 + "\n")
        
    except Exception as e:
        print(f"\n❌ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
