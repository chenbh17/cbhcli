"""自定义异常类"""


class CBHCLIError(Exception):
    """CBHCLI基础异常"""
    pass


class ModelNotConfiguredError(CBHCLIError):
    """模型未配置异常"""
    pass


class ToolExecutionError(CBHCLIError):
    """工具执行错误异常"""
    pass


class ContextLimitExceededError(CBHCLIError):
    """上下文超限异常"""
    pass


class AgentNotFoundError(CBHCLIError):
    """Agent未找到异常"""
    pass


class SessionError(CBHCLIError):
    """会话错误异常"""
    pass
