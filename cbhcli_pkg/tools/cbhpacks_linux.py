"""cbhpacks Linux连接工具 - con_linux 模块封装

包含工具类:
ConLinuxTool: SSH连接Linux服务器执行命令、文件传输、Hadoop/Hive管理

依赖关系:
- 独立使用，需要SSH连接信息
- con_linux 用于远程执行shell命令
- data_trans_linux 用于文件传输
- jps/hadoop/start_hive 用于Hadoop/Hive集群管理
"""
import os
import json
import pandas as pd
from cbhcli_pkg.tools.registry import BaseTool, ToolResult


class ConLinuxTool(BaseTool):
    """cbhpacks Linux连接工具"""

    @property
    def name(self) -> str:
        return "cbhpacks_con_linux"

    @property
    def description(self) -> str:
        return (
            "cbhpacks Linux连接工具 - SSH执行命令、文件传输、Hadoop/Hive管理。\n\n"
            "【可用 method】\n"
            "  con_linux       - SSH执行shell命令\n"
            "  data_trans_linux - 文件传输（上传put/下载get）\n"
            "  jps             - 查看Java进程\n"
            "  hadoop          - 启动/停止Hadoop集群\n"
            "  start_hive      - 启动Hive服务\n\n"
            "【依赖关系】\n"
            "  - 独立使用，需要SSH连接信息\n"
            "  - to_hive（cbhpacks_con_sql）依赖此工具进行文件传输\n"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "description": "调用的方法名",
                    "enum": ["con_linux", "data_trans_linux", "jps", "hadoop", "start_hive"]
                },
                "shell": {
                    "type": "string",
                    "description": "要执行的shell命令（con_linux使用）"
                },
                "user": {
                    "type": "string",
                    "description": "SSH用户名",
                    "enum": ["chenbh17", "root"],
                    "default": "chenbh17"
                },
                "local_loc": {
                    "type": "string",
                    "description": "data_trans_linux: 本地文件路径"
                },
                "client_loc": {
                    "type": "string",
                    "description": "data_trans_linux: 远程服务器文件路径",
                    "default": "/media/chenbh17/cbhssd/invest/data/to_hive.csv"
                },
                "transfer_method": {
                    "type": "string",
                    "description": "data_trans_linux: 传输方式 put(上传)/get(下载)",
                    "enum": ["put", "get"],
                    "default": "put"
                },
                "hadoop_type": {
                    "type": "string",
                    "description": "hadoop: 启动/停止",
                    "enum": ["start", "stop"],
                    "default": "start"
                }
            },
            "required": ["method"]
        }

    def execute(self, **kwargs) -> ToolResult:
        try:
            method = kwargs.get("method")
            shell = kwargs.get("shell", "")
            user = kwargs.get("user", "chenbh17")
            local_loc = kwargs.get("local_loc")
            client_loc = kwargs.get("client_loc", "/media/chenbh17/cbhssd/invest/data/to_hive.csv")
            transfer_method = kwargs.get("transfer_method", "put")
            hadoop_type = kwargs.get("hadoop_type", "start")

            from cbhpacks.con_linux import con_linux, data_trans_linux, jps, hadoop, start_hive

            output_files = []
            result_text = ""

            if method == "con_linux":
                if not shell:
                    return ToolResult(success=False, output="", error="需要提供shell参数")
                con_linux(shell=shell, user=user)
                result_text = f"SSH命令执行完成 (user={user})"

            elif method == "data_trans_linux":
                if not local_loc:
                    return ToolResult(success=False, output="", error="需要提供local_loc参数")
                data_trans_linux(local_loc=local_loc, client_loc=client_loc, method=transfer_method)
                result_text = f"文件传输完成 ({transfer_method}): {local_loc} ↔ {client_loc}"

            elif method == "jps":
                jps()
                result_text = "Java进程列表已显示"

            elif method == "hadoop":
                result = hadoop(hadoop_type)
                result_text = f"Hadoop操作结果: {result}"

            elif method == "start_hive":
                result = start_hive()
                result_text = f"Hive启动结果: {result}"

            else:
                return ToolResult(success=False, output="", error=f"未知方法: {method}")

            output = (
                f"📊 cbhpacks_con_linux.{method} 执行完成\n\n"
                f"📋 结果:\n{result_text}"
            )
            return ToolResult(success=True, output=output)

        except Exception as e:
            import traceback
            return ToolResult(success=False, output="", error=f"执行失败: {str(e)}\n{traceback.format_exc()}")
