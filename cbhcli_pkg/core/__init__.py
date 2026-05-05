"""核心模块 - Agent、Session、Model管理"""

from .app import CBHCLIApp
from .agent import AgentManager, AgentConfig, AgentPersona
from .session import Session, Message, ContextWindow
from .model import LLMClient
from .subagent import SubAgentScheduler
from .tool_executor import ToolExecutor
from .ai_handler import AIHandler
from .response_cleaner import ResponseCleaner
from .errors import (
    CBHCLIError,
    ModelNotConfiguredError,
    ToolExecutionError,
    ContextLimitExceededError,
    AgentNotFoundError,
    SessionError
)
from . import constants

__all__ = [
    # 主应用
    'CBHCLIApp',
    
    # Agent管理
    'AgentManager',
    'AgentConfig',
    'AgentPersona',
    
    # 会话管理
    'Session',
    'Message',
    'ContextWindow',
    
    # 模型
    'LLMClient',
    
    # 子Agent调度
    'SubAgentScheduler',
    
    # 工具执行
    'ToolExecutor',
    
    # AI处理
    'AIHandler',
    'ResponseCleaner',
    
    # 异常
    'CBHCLIError',
    'ModelNotConfiguredError',
    'ToolExecutionError',
    'ContextLimitExceededError',
    'AgentNotFoundError',
    'SessionError',
    
    # 常量
    'constants',
]
