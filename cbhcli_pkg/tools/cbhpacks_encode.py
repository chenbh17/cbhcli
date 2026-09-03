"""cbhpacks 特征编码工具 - cols_encode 模块封装

每次执行自动保存可复现的Python源码脚本到输出目录。
执行结果变量(ce/encode_data等)自动注入 python 会话，可在 python 工具中直接使用。
"""
import os
import json
import pandas as pd
from cbhcli_pkg.tools.registry import ToolResult
from cbhcli_pkg.tools.cbhpacks_session import CbhpacksSessionTool
from cbhcli_pkg.tools.cbhpacks_guard import format_findings, Finding


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


class ColsEncodeTool(CbhpacksSessionTool):
    @property
    def name(self): return "cbhpacks_cols_encode"

    @property
    def description(self):
        return (
            "cbhpacks 特征编码工具 - 7种编码方法。每次执行自动保存可复现源码。\n"
            "执行后结果变量自动注入 python 会话：ce(cols_encode实例)/df/encode_data\n\n"
            "【method】data_to_sigmoid/data_to_sc/data_to_minmax/data_to_softmax/bins_to_num/str_to_num/data_to_woe\n\n"
            "【⚠️ 注意事项】data_to_woe 每次调用都会触发数据穿越警告：\n"
            "本工具基于当前数据集自身分箱计算 WOE，仅适合训练集自身转换（且与 bins 工具分箱状态不共享）；\n"
            "评分卡场景请一律使用 cbhpacks_bins_model.data_to_woe（训练集参数继承+测试集 woe_mapping_pkl 防穿越）。"
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
        self._findings_buffer = []  # 清空上次残留（异常路径可能未取走）
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
                # ═══ 修复(v5.3.1) Bug 5: C2 泄漏防护补位 ═══
                # 本工具的 data_to_woe 基于**当前数据集自身**分箱计算 WOE：
                # 对测试集/OOT 调用 = WOE 在测试集上计算 = 数据穿越。
                # bins 工具的 data_to_woe 有 woe_mapping_pkl 防穿越参数，本工具没有——
                # 此处给确定性警告（护栏层不依赖 description 文字劝诫）。
                findings = [Finding(
                    "WARN", "C2",
                    "cbhpacks_cols_encode.data_to_woe 基于当前数据集自身分箱计算 WOE，"
                    "若当前数据集是测试集/OOT 则构成数据穿越（WOE 只能在训练集计算）",
                    "训练/测试集的 WOE 转换请改用 cbhpacks_bins_model.data_to_woe："
                    "训练集直接转换（与 comp_woe_iv 参数一致）；"
                    "测试集/OOT 传 woe_mapping_pkl（训练集产出的 woe_mapping_*.pkl）不重新分箱")]
                harness_note = format_findings(findings)
                self._log_findings(findings)
                woe_df, woe_dic = ce.data_to_woe()
                output_files += [f"  ✅ {path}/woe_encode_detail.pkl", f"  ✅ {path}/woe_mapping_*.pkl"]
                result_text = f"WOE编码完成，shape: {woe_df.shape}，映射变量: {len(woe_dic)}"
                if harness_note:
                    result_text += f"\n\n{harness_note}"
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

            # 结果变量注入 python 会话
            encode_data = woe_df if method == "data_to_woe" else data
            self._expose(ce=ce, df=df, encode_data=encode_data)

            return ToolResult(success=True, harness_findings=self._pop_findings(), output=(
                f"📊 cbhpacks_cols_encode.{method} 执行完成\n\n"
                f"📁 输出文件:\n" + "\n".join(output_files) + f"\n"
                f"  📁 输出目录: {path}/\n\n"
                f"📋 结果:\n{result_text}\n\n"
                f"💡 已注入 python 会话变量: ce, df, encode_data"
            ))

        except Exception as e:
            import traceback
            return ToolResult(success=False, output="", error=f"执行失败: {str(e)}\n{traceback.format_exc()}")
