"""cbhpacks 数据库连接工具 - con_sql 模块封装

包含工具类:
1. ConSqlTool: ClickHouse/MySQL/Hive 数据库连接与SQL执行
2. RfmSqlTool: RFMS范式特征衍生SQL生成

依赖关系:
- 独立使用，无特殊上游依赖
- con_mysql/con_hive 需要数据库连接信息
- to_hive 需要 Linux 服务器连接（依赖 con_linux）
- rfms_sql 生成的 SQL 需要通过 con_hive 执行
"""
import os
import json
import pandas as pd
from cbhcli_pkg.tools.registry import BaseTool, ToolResult


class ConSqlTool(BaseTool):
    """cbhpacks 数据库连接工具"""

    @property
    def name(self) -> str:
        return "cbhpacks_con_sql"

    @property
    def description(self) -> str:
        return (
            "cbhpacks 数据库连接工具 - ClickHouse/MySQL/Hive SQL执行与数据导入。\n\n"
            "【可用 method】\n"
            "  chrun          - ClickHouse执行SQL（不返回数据）\n"
            "  chdf           - ClickHouse查询数据（返回DataFrame）\n"
            "  con_mysql      - MySQL执行SQL/查询数据\n"
            "  con_hive       - Hive执行SQL/查询数据\n"
            "  get_create_table - 根据DataFrame生成Hive建表SQL\n"
            "  to_hive        - 将数据导入Hive表（⚠️需要con_linux连接）\n"
            "  rfms_sql       - RFMS范式特征衍生SQL生成\n\n"
            "【依赖关系】\n"
            "  - chrun/chdf: 仅需ClickHouse连接信息（默认已配置）\n"
            "  - con_mysql: 需要MySQL连接信息（host/port/user/password/database）\n"
            "  - con_hive: 需要Hive连接信息（默认已配置）\n"
            "  - to_hive: 依赖 con_linux（Linux服务器SSH连接）用于文件传输\n"
            "  - rfms_sql: 生成的SQL需通过con_hive执行\n"
            "  - get_create_table/to_hive 需要CSV数据文件作为输入\n"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "description": "调用的方法名",
                    "enum": ["chrun", "chdf", "con_mysql", "con_hive",
                             "get_create_table", "to_hive", "rfms_sql"]
                },
                "sql": {
                    "type": "string",
                    "description": "SQL语句（chrun/chdf/con_mysql/con_hive使用）"
                },
                "csv_path": {
                    "type": "string",
                    "description": "数据文件路径（get_create_table/to_hive使用）"
                },
                "table_name": {
                    "type": "string",
                    "description": "Hive表名（get_create_table/to_hive使用）"
                },
                "local_loc": {
                    "type": "string",
                    "description": "to_hive: 本地CSV文件保存路径"
                },
                "shell_loc": {
                    "type": "string",
                    "description": "to_hive: Linux服务器上的文件目录",
                    "default": "/media/chenbh17/cbhssd/invest/data/"
                },
                "method_type": {
                    "type": "string",
                    "description": "to_hive: 导入方式 overwrite/append",
                    "enum": ["overwrite", "append"],
                    "default": "overwrite"
                },
                "encoding": {
                    "type": "string",
                    "description": "编码格式（get_create_table/to_hive使用）",
                    "default": "UTF-8"
                },
                "partition": {
                    "type": "boolean",
                    "description": "是否分区（get_create_table/to_hive使用）",
                    "default": False
                },
                "partition_col": {
                    "type": "string",
                    "description": "分区列（格式: 'col_name type'，如 'year string'）",
                    "default": "year string"
                },
                "bucket": {
                    "type": "boolean",
                    "description": "是否分桶（get_create_table/to_hive使用）",
                    "default": False
                },
                "bucket_col": {
                    "type": "string",
                    "description": "分桶列名"
                },
                "bucket_num": {
                    "type": "integer",
                    "description": "分桶数",
                    "default": 10
                },
                "host": {
                    "type": "string",
                    "description": "MySQL服务器地址",
                    "default": "192.168.10.200"
                },
                "port": {
                    "type": "integer",
                    "description": "MySQL端口",
                    "default": 3306
                },
                "user": {
                    "type": "string",
                    "description": "MySQL用户名",
                    "default": "hive"
                },
                "password": {
                    "type": "string",
                    "description": "MySQL密码",
                    "default": "hive"
                },
                "database": {
                    "type": "string",
                    "description": "MySQL数据库名",
                    "default": "dev"
                },
                "charset": {
                    "type": "string",
                    "description": "MySQL字符集"
                },
                "new_table": {
                    "type": "string",
                    "description": "rfms_sql: 新表名"
                },
                "origin_table": {
                    "type": "string",
                    "description": "rfms_sql: 原始表名"
                },
                "day_list": {
                    "type": "string",
                    "description": "rfms_sql: 时间间隔列表，JSON格式",
                    "default": "[5,20,120,250]"
                },
                "output_csv": {
                    "type": "string",
                    "description": "chdf: 查询结果保存路径（可选）"
                }
            },
            "required": ["method"]
        }

    def execute(self, **kwargs) -> ToolResult:
        try:
            method = kwargs.get("method")
            sql = kwargs.get("sql", "")
            csv_path = kwargs.get("csv_path")
            table_name = kwargs.get("table_name")
            local_loc = kwargs.get("local_loc")
            shell_loc = kwargs.get("shell_loc", "/media/chenbh17/cbhssd/invest/data/")
            method_type = kwargs.get("method_type", "overwrite")
            encoding = kwargs.get("encoding", "UTF-8")
            partition = kwargs.get("partition", False)
            partition_col = kwargs.get("partition_col", "year string")
            bucket = kwargs.get("bucket", False)
            bucket_col = kwargs.get("bucket_col")
            bucket_num = kwargs.get("bucket_num", 10)
            host = kwargs.get("host", "192.168.10.200")
            port = kwargs.get("port", 3306)
            user = kwargs.get("user", "hive")
            password = kwargs.get("password", "hive")
            database = kwargs.get("database", "dev")
            charset = kwargs.get("charset")
            new_table = kwargs.get("new_table")
            origin_table = kwargs.get("origin_table")
            day_list_str = kwargs.get("day_list", "[5,20,120,250]")
            output_csv = kwargs.get("output_csv")

            from cbhpacks.con_sql import chrun, chdf, con_mysql, con_hive, get_create_table, to_hive, rfms_sql

            output_files = []
            result_text = ""

            if method == "chrun":
                if not sql:
                    return ToolResult(success=False, output="", error="需要提供sql参数")
                result = chrun(sql)
                result_text = f"ClickHouse执行完成: {result}"

            elif method == "chdf":
                if not sql:
                    return ToolResult(success=False, output="", error="需要提供sql参数")
                result = chdf(sql)
                if output_csv and isinstance(result, pd.DataFrame):
                    os.makedirs(os.path.dirname(output_csv) or ".", exist_ok=True)
                    result.to_csv(output_csv, index=False)
                    output_files.append(f"  ✅ {output_csv} — 查询结果")
                if isinstance(result, pd.DataFrame):
                    result_text = f"查询结果 shape: {result.shape}\n前5行:\n{result.head().to_string()}"
                elif isinstance(result, list):
                    result_text = f"返回 {len(result)} 个结果集"
                else:
                    result_text = f"ClickHouse查询完成"

            elif method == "con_mysql":
                if not sql:
                    return ToolResult(success=False, output="", error="需要提供sql参数")
                result = con_mysql(sql=sql, host=host, port=port, user=user,
                                   password=password, database=database, charset=charset)
                if output_csv and isinstance(result, pd.DataFrame):
                    os.makedirs(os.path.dirname(output_csv) or ".", exist_ok=True)
                    result.to_csv(output_csv, index=False)
                    output_files.append(f"  ✅ {output_csv} — 查询结果")
                if isinstance(result, pd.DataFrame):
                    result_text = f"MySQL查询结果 shape: {result.shape}\n前5行:\n{result.head().to_string()}"
                else:
                    result_text = f"MySQL执行完成"

            elif method == "con_hive":
                if not sql:
                    return ToolResult(success=False, output="", error="需要提供sql参数")
                result = con_hive(sql=sql)
                if output_csv and isinstance(result, pd.DataFrame):
                    os.makedirs(os.path.dirname(output_csv) or ".", exist_ok=True)
                    result.to_csv(output_csv, index=False)
                    output_files.append(f"  ✅ {output_csv} — 查询结果")
                if isinstance(result, pd.DataFrame):
                    result_text = f"Hive查询结果 shape: {result.shape}\n前5行:\n{result.head().to_string()}"
                else:
                    result_text = f"Hive执行完成"

            elif method == "get_create_table":
                if not csv_path or not table_name:
                    return ToolResult(success=False, output="", error="需要提供csv_path和table_name参数")
                data = pd.read_csv(csv_path)
                result = get_create_table(data=data, table_name=table_name, encoding=encoding,
                                          partition=partition, bucket=bucket,
                                          partition_col=partition_col, bucket_col=bucket_col,
                                          bucket_num=bucket_num)
                result_text = f"建表SQL:\n{result}"

            elif method == "to_hive":
                if not csv_path or not table_name or not local_loc:
                    return ToolResult(success=False, output="", error="需要提供csv_path/table_name/local_loc参数")
                data = pd.read_csv(csv_path)
                result = to_hive(data=data, table_name=table_name, local_loc=local_loc,
                                 shell_loc=shell_loc, method=method_type, encoding=encoding,
                                 partition=partition, bucket=bucket,
                                 partition_col=partition_col, bucket_col=bucket_col,
                                 bucket_num=bucket_num)
                result_text = f"Hive导入结果: {result}"

            elif method == "rfms_sql":
                if not csv_path or not new_table or not origin_table:
                    return ToolResult(success=False, output="", error="需要提供csv_path/new_table/origin_table参数")
                data = pd.read_csv(csv_path)
                cols = [c for c in data.columns if c not in ['stock_code', 'trade_date', 'mth', 'year']]
                day_list = json.loads(day_list_str) if isinstance(day_list_str, str) else day_list_str
                result = rfms_sql(data=data, cols=cols, new_table=new_table,
                                  origin_table=origin_table, day_list=day_list)
                result_text = f"RFMS特征衍生: {result}"

            else:
                return ToolResult(success=False, output="", error=f"未知方法: {method}")

            output = (
                f"📊 cbhpacks_con_sql.{method} 执行完成\n\n"
                f"📁 输出文件:\n" + ("\n".join(output_files) if output_files else "  无文件输出") + "\n\n"
                f"📋 结果:\n{result_text}"
            )
            return ToolResult(success=True, output=output)

        except Exception as e:
            import traceback
            return ToolResult(success=False, output="", error=f"执行失败: {str(e)}\n{traceback.format_exc()}")
