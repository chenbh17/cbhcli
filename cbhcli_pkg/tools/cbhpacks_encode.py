"""cbhpacks 特征编码工具 - cols_encode 模块封装

每次执行自动保存可复现的Python源码脚本到输出目录。
"""
import os
import json
import pandas as pd
from cbhcli_pkg.tools.registry import BaseTool, ToolResult


def auto_version_path(base_path):
    if not os.path.exists(base_path) or not os.listdir(base_path):
        return base_path
    v = 2
    while os.path.exists(f"{base_path}_v{v}") and os.listdir(f"{base_path}_v{v}"):
        v += 1
    return f"{base_path}_v{v}"


def save_script(output_dir, filename, code):
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, filename), 'w', encoding='utf-8') as f:
        f.write(code)


class ColsEncodeTool(BaseTool):
    @property
    def name(self): return "cbhpacks_cols_encode"

    @property
    def description(self):
        return (
            "cbhpacks 特征编码工具 - 7种编码方法。每次执行自动保存可复现源码。\n\n"
            "【method】data_to_sigmoid/data_to_sc/data_to_minmax/data_to_softmax/bins_to_num/str_to_num/data_to_woe"
        )

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["data_to_sigmoid", "data_to_sc", "data_to_minmax", "data_to_softmax", "bins_to_num", "str_to_num", "data_to_woe"]},
                "csv_path": {"type": "string"},
                "cols": {"type": "string", "description": "JSON格式"},
                "target": {"type": "string", "default": "target"},
                "bins_type": {"type": "string", "default": "eq_cnt"},
                "group": {"type": "integer", "default": 10},
                "nan": {"type": "number", "default": -9999},
                "adj_bin": {"type": "boolean", "default": False},
                "min_group": {"type": "integer", "default": 2},
                "path": {"type": "string", "default": "step1_cols_encode"},
                "output_csv": {"type": "string"}
            },
            "required": ["method", "csv_path", "cols"]
        }

    def execute(self, **kwargs):
        try:
            method = kwargs.get("method")
            csv_path = kwargs.get("csv_path")
            cols = json.loads(kwargs.get("cols")) if isinstance(kwargs.get("cols"), str) else kwargs.get("cols")
            target = kwargs.get("target", "target")
            bins_type = kwargs.get("bins_type", "eq_cnt")
            group = kwargs.get("group", 10)
            nan = kwargs.get("nan", -9999)
            adj_bin = kwargs.get("adj_bin", False)
            min_group = kwargs.get("min_group", 2)
            path = kwargs.get("path", "step1_cols_encode")
            output_csv = kwargs.get("output_csv")

            path = auto_version_path(path)
            if not os.path.exists(csv_path):
                return ToolResult(success=False, output="", error=f"文件不存在: {csv_path}")
            df = pd.read_csv(csv_path)

            from cbhpacks.cols_encode import cols_encode
            ce = cols_encode(df=df, cols=cols, bins_type=bins_type, group=group,
                           target=target, nan=nan, path=path, adj_bin=adj_bin, min_group=min_group)

            output_files = []
            result_text = ""
            script_body = ""

            if method == "data_to_sigmoid":
                data = ce.data_to_sigmoid()
                output_files.append(f"  ✅ {path}/sigmoid_encode_data.csv")
                result_text = f"Sigmoid编码完成，shape: {data.shape}"
                script_body = "data = ce.data_to_sigmoid()\ndata.to_csv(f'{path}/sigmoid_encode_data.csv', index=False)"

            elif method == "data_to_sc":
                data = ce.data_to_sc()
                output_files += [f"  ✅ {path}/sc_encode_data.csv", f"  ✅ {path}/z_score_model.pkl"]
                result_text = f"Z-Score标准化完成，shape: {data.shape}"
                script_body = "data = ce.data_to_sc()\nprint(f'Z-Score标准化完成，shape: {data.shape}')"

            elif method == "data_to_minmax":
                data = ce.data_to_minmax()
                output_files += [f"  ✅ {path}/minmax_encode_data.csv", f"  ✅ {path}/min_max_model.pkl"]
                result_text = f"MinMax归一化完成，shape: {data.shape}"
                script_body = "data = ce.data_to_minmax()\nprint(f'MinMax归一化完成，shape: {data.shape}')"

            elif method == "data_to_softmax":
                data = ce.data_to_softmax()
                output_files.append(f"  ✅ {path}/softmax_encode_data.csv")
                result_text = f"Softmax转换完成，shape: {data.shape}"
                script_body = "data = ce.data_to_softmax()\nprint(f'Softmax转换完成，shape: {data.shape}')"

            elif method == "bins_to_num":
                data, details = ce.bins_to_num()
                output_files += [f"  ✅ {path}/bins_encode_data.csv", f"  ✅ {path}/bins_encode_detail.pkl"]
                result_text = f"分箱编号编码完成，shape: {data.shape}，映射变量: {len(details)}"
                script_body = "data, details = ce.bins_to_num()\nprint(f'分箱编号编码完成，映射变量: {len(details)}')"

            elif method == "str_to_num":
                data, details = ce.str_to_num()
                output_files += [f"  ✅ {path}/count_encode_data.csv", f"  ✅ {path}/count_encode_detail.pkl"]
                result_text = f"字符串编码完成，shape: {data.shape}，映射变量: {len(details)}"
                script_body = "data, details = ce.str_to_num()\nprint(f'字符串编码完成，映射变量: {len(details)}')"

            elif method == "data_to_woe":
                woe_df, woe_dic = ce.data_to_woe()
                output_files += [f"  ✅ {path}/woe_encode_detail.pkl", f"  ✅ {path}/woe_mapping_*.pkl"]
                result_text = f"WOE编码完成，shape: {woe_df.shape}，映射变量: {len(woe_dic)}"
                script_body = "woe_df, woe_dic = ce.data_to_woe()\nprint(f'WOE编码完成，映射变量: {len(woe_dic)}')"
            else:
                return ToolResult(success=False, output="", error=f"未知方法: {method}")

            if output_csv:
                os.makedirs(os.path.dirname(output_csv) or ".", exist_ok=True)
                (woe_df if method == "data_to_woe" else data).to_csv(output_csv, index=False)
                output_files.append(f"  ✅ {output_csv}")

            script_code = f'''import pandas as pd
from cbhpacks.cols_encode import cols_encode

df = pd.read_csv("{csv_path}")
ce = cols_encode(df=df, cols={cols}, bins_type="{bins_type}", group={group},
    target="{target}", nan={nan}, path="{path}", adj_bin={adj_bin}, min_group={min_group})
{script_body}
'''
            save_script(path, f"run_{method}.py", script_code)
            output_files.append(f"  ✅ {path}/run_{method}.py — 可复现源码")

            return ToolResult(success=True, output=(
                f"📊 cbhpacks_cols_encode.{method} 执行完成\n\n"
                f"📁 输出文件:\n" + "\n".join(output_files) + f"\n"
                f"  📁 输出目录: {path}/\n\n"
                f"📋 结果:\n{result_text}"
            ))

        except Exception as e:
            import traceback
            return ToolResult(success=False, output="", error=f"执行失败: {str(e)}\n{traceback.format_exc()}")
