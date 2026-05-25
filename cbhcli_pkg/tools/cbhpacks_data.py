"""cbhpacks 测试数据生成工具 - models_for_try 模块封装

工具类:
GetRandomDataTool: 生成随机测试数据集

依赖关系:
- 独立使用，无特殊上游依赖
- 产出的测试数据可供所有其他cbhpacks工具使用
"""
import os
import json
import pandas as pd
from cbhcli_pkg.tools.registry import BaseTool, ToolResult


class GetRandomDataTool(BaseTool):
    """cbhpacks 测试数据生成工具"""

    @property
    def name(self) -> str:
        return "cbhpacks_get_random_data"

    @property
    def description(self) -> str:
        return (
            "cbhpacks 测试数据生成工具 - 生成随机数据集用于测试。\n\n"
            "【功能】\n"
            "  生成包含月份、多个特征列（整数/浮点数/字符串）、目标变量的随机数据集。\n"
            "  数据包含: id, mth(月份), target(目标变量), col1~col10(特征)\n"
            "  特征类型: 整数(col1/2/6/7/8/9)、浮点数(col3/4/5)、字符串(col10)\n"
            "  自动引入20%随机缺失值（col4~col9）\n\n"
            "【依赖关系】\n"
            "  - 独立使用，无上游依赖\n"
            "  - 产出的CSV可供所有cbhpacks工具使用\n"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "min_edge": {
                    "type": "integer",
                    "description": "随机整数最小值",
                    "default": 0
                },
                "max_edge": {
                    "type": "integer",
                    "description": "随机整数最大值",
                    "default": 100
                },
                "num": {
                    "type": "integer",
                    "description": "样本数量",
                    "default": 1000
                },
                "mth_cnt": {
                    "type": "integer",
                    "description": "月份个数（从202401开始）",
                    "default": 6
                },
                "output_csv": {
                    "type": "string",
                    "description": "输出CSV文件路径",
                    "default": "test_random_data.csv"
                }
            },
            "required": []
        }

    def execute(self, **kwargs) -> ToolResult:
        try:
            min_edge = kwargs.get("min_edge", 0)
            max_edge = kwargs.get("max_edge", 100)
            num = kwargs.get("num", 1000)
            mth_cnt = kwargs.get("mth_cnt", 6)
            output_csv = kwargs.get("output_csv", "test_random_data.csv")

            from cbhpacks.models_for_try import get_random_data

            data = get_random_data(min_edge=min_edge, max_edge=max_edge, num=num, mth_cnt=mth_cnt)

            # 保存CSV
            os.makedirs(os.path.dirname(output_csv) or ".", exist_ok=True)
            data.to_csv(output_csv, index=False)

            output_files = [
                f"  ✅ {output_csv} — 测试数据集"
            ]

            result_text = (
                f"测试数据生成完成\n"
                f"  样本数: {num}\n"
                f"  月份范围: 202401 ~ {202401 + mth_cnt - 1}\n"
                f"  特征数: 10 (col1~col10)\n"
                f"  数据shape: {data.shape}\n\n"
                f"数据预览:\n{data.head(10).to_string()}\n\n"
                f"数据类型:\n{data.dtypes.to_string()}\n\n"
                f"缺失值统计:\n{data.isnull().sum().to_string()}"
            )

            output = (
                f"📊 cbhpacks_get_random_data 执行完成\n\n"
                f"📁 输出文件:\n" + "\n".join(output_files) + "\n\n"
                f"📋 结果:\n{result_text}\n\n"
                f"💡 提示: 生成的CSV可供所有cbhpacks工具使用，如:\n"
                f"  - cbhpacks_bins_model: 分箱WOE/IV计算\n"
                f"  - cbhpacks_cols_encode: 特征编码\n"
                f"  - cbhpacks_cols_select: 特征筛选\n"
                f"  - cbhpacks_binary_model: 模型训练"
            )
            return ToolResult(success=True, output=output)

        except Exception as e:
            import traceback
            return ToolResult(success=False, output="", error=f"执行失败: {str(e)}\n{traceback.format_exc()}")
