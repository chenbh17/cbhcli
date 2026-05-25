"""cbhpacks 数据预处理工具 - preprocess 模块封装

三个工具类: ColsOperateTool / DescDfTool / DescColTool
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


class ColsOperateTool(BaseTool):
    @property
    def name(self): return "cbhpacks_cols_operate"

    @property
    def description(self):
        return "cbhpacks 列操作工具 - 炸裂/转置/分割/日期转换/分词。每次执行自动保存可复现源码。"

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["col_explode", "col_to_T", "col_to_cols", "date_col_trans", "date_mth_year", "jieba_trans"]},
                "csv_path": {"type": "string"},
                "mean_key": {"type": "string"},
                "col": {"type": "string"},
                "date_col": {"type": "string"},
                "explode_method": {"type": "string", "default": ","},
                "col_split_method": {"type": "string", "default": ","},
                "date_type": {"type": "string", "default": "%Y%m%d"},
                "jieba_method": {"type": "string", "default": ","},
                "output_csv": {"type": "string"}
            },
            "required": ["method", "csv_path"]
        }

    def execute(self, **kwargs):
        try:
            method = kwargs.get("method")
            csv_path = kwargs.get("csv_path")
            mean_key = kwargs.get("mean_key", "")
            col = kwargs.get("col", "")
            date_col = kwargs.get("date_col", "")
            explode_method = kwargs.get("explode_method", ",")
            col_split_method = kwargs.get("col_split_method", ",")
            date_type = kwargs.get("date_type", "%Y%m%d")
            jieba_method = kwargs.get("jieba_method", ",")
            output_csv = kwargs.get("output_csv")

            if not os.path.exists(csv_path):
                return ToolResult(success=False, output="", error=f"文件不存在: {csv_path}")
            df = pd.read_csv(csv_path)

            from cbhpacks.preprocess import cols_operate
            co = cols_operate(df=df, mean_key=mean_key, col=col, date_col=date_col,
                            explode_method=explode_method, col_split_method=col_split_method,
                            date_type=date_type, jieba_method=jieba_method)

            method_map = {"col_explode": co.col_explode, "col_to_T": co.col_to_T, "col_to_cols": co.col_to_cols,
                         "date_col_trans": co.date_col_trans, "date_mth_year": co.date_mth_year, "jieba_trans": co.jieba_trans}
            if method not in method_map:
                return ToolResult(success=False, output="", error=f"未知方法: {method}")

            data = method_map[method]()

            if output_csv:
                os.makedirs(os.path.dirname(output_csv) or ".", exist_ok=True)
                data.to_csv(output_csv, index=False)

            # 保存源码
            out_dir = os.path.dirname(output_csv) if output_csv else "."
            params = [f'df=pd.read_csv("{csv_path}")']
            if mean_key: params.append(f'mean_key="{mean_key}"')
            if col: params.append(f'col="{col}"')
            if date_col: params.append(f'date_col="{date_col}"')
            if explode_method != ",": params.append(f'explode_method="{explode_method}"')
            if col_split_method != ",": params.append(f'col_split_method="{col_split_method}"')
            if date_type != "%Y%m%d": params.append(f'date_type="{date_type}"')
            if jieba_method != ",": params.append(f'jieba_method="{jieba_method}"')

            save_script(out_dir, f"run_{method}.py", f'''import pandas as pd
from cbhpacks.preprocess import cols_operate
{", ".join(params[:1])}
co = cols_operate({", ".join(params)})
data = co.{method}()
data.to_csv("{output_csv or f'{method}_result.csv'}", index=False)
print(f"处理完成，shape: {{data.shape}}")
print(data.head())
''')

            return ToolResult(success=True, output=(
                f"📊 cbhpacks_cols_operate.{method} 执行完成\n\n"
                f"📋 shape: {data.shape}\n前5行:\n{data.head().to_string()}"
            ))
        except Exception as e:
            import traceback
            return ToolResult(success=False, output="", error=f"执行失败: {str(e)}\n{traceback.format_exc()}")


class DescDfTool(BaseTool):
    @property
    def name(self): return "cbhpacks_desc_df"

    @property
    def description(self):
        return "cbhpacks 数据集描述统计工具。每次执行自动保存可复现源码。"

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["get_rpt"], "default": "get_rpt"},
                "csv_path": {"type": "string"},
                "cols": {"type": "string", "description": "JSON格式"},
                "cat_cols": {"type": "string", "default": "[]"},
                "path": {"type": "string", "default": "step0_desc_result"}
            },
            "required": ["csv_path", "cols"]
        }

    def execute(self, **kwargs):
        try:
            csv_path = kwargs.get("csv_path")
            cols = json.loads(kwargs.get("cols")) if isinstance(kwargs.get("cols"), str) else kwargs.get("cols")
            cat_cols = json.loads(kwargs.get("cat_cols", "[]")) if isinstance(kwargs.get("cat_cols"), str) else kwargs.get("cat_cols", [])
            path = kwargs.get("path", "step0_desc_result")

            if not os.path.exists(csv_path):
                return ToolResult(success=False, output="", error=f"文件不存在: {csv_path}")
            df = pd.read_csv(csv_path)

            from cbhpacks.preprocess import desc_df
            dd = desc_df(df=df, cols=cols, cat_cols=cat_cols, path=path)
            num_report, cat_report = dd.get_rpt()

            save_script(path, "run_get_rpt.py", f'''import pandas as pd
from cbhpacks.preprocess import desc_df
df = pd.read_csv("{csv_path}")
dd = desc_df(df=df, cols={cols}, cat_cols={cat_cols}, path="{path}")
num_report, cat_report = dd.get_rpt()
print(f"数值型特征: {{len(num_report)}}个")
print(f"类别型特征: {{len(cat_report)}}个")
''')

            return ToolResult(success=True, output=(
                f"📊 cbhpacks_desc_df.get_rpt 执行完成\n\n"
                f"📁 输出: {path}/desc_num_rpt.xlsx, desc_cat_rpt.xlsx\n"
                f"  ✅ {path}/run_get_rpt.py — 可复现源码\n\n"
                f"📋 数值型: {len(num_report)}个, 类别型: {len(cat_report)}个"
            ))
        except Exception as e:
            import traceback
            return ToolResult(success=False, output="", error=f"执行失败: {str(e)}\n{traceback.format_exc()}")


class DescColTool(BaseTool):
    @property
    def name(self): return "cbhpacks_desc_col"

    @property
    def description(self):
        return "cbhpacks 单变量分析工具 - 描述/相关性/有监督/异常值检测。每次执行自动保存可复现源码。"

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["desc_", "relative_", "supervised_", "easy_od", "feat_card"]},
                "csv_path": {"type": "string"},
                "col": {"type": "string"},
                "cols": {"type": "string", "description": "JSON格式"},
                "target": {"type": "string", "default": "target"},
                "cat_cols": {"type": "string", "default": "[]"},
                "corr_threshold": {"type": "number", "default": 0.5},
                "path": {"type": "string", "default": "step0_single_col_desc_result"},
                "how": {"type": "string", "enum": ["whisker", "3sigma"], "default": "whisker"}
            },
            "required": ["method", "csv_path", "col", "cols"]
        }

    def execute(self, **kwargs):
        try:
            method = kwargs.get("method")
            csv_path = kwargs.get("csv_path")
            col = kwargs.get("col")
            cols = json.loads(kwargs.get("cols")) if isinstance(kwargs.get("cols"), str) else kwargs.get("cols")
            target = kwargs.get("target", "target")
            cat_cols = json.loads(kwargs.get("cat_cols", "[]")) if isinstance(kwargs.get("cat_cols"), str) else kwargs.get("cat_cols", [])
            corr_threshold = kwargs.get("corr_threshold", 0.5)
            path = kwargs.get("path", "step0_single_col_desc_result")
            how = kwargs.get("how", "whisker")

            path = auto_version_path(path)
            if not os.path.exists(csv_path):
                return ToolResult(success=False, output="", error=f"文件不存在: {csv_path}")
            df = pd.read_csv(csv_path)

            from cbhpacks.preprocess import desc_col
            dc = desc_col(df=df, target=target, col=col, cols=cols,
                        cat_cols=cat_cols, corr_threshold=corr_threshold, path=path)

            output_files = []
            result_text = ""
            script_body = ""

            if method == "desc_":
                dc.desc_()
                output_files.append(f"  ✅ {path}/{col}_desc.xlsx")
                result_text = f"变量 {col} 描述性分析完成"
                script_body = "dc.desc_()"

            elif method == "relative_":
                dc.relative_()
                output_files.append(f"  ✅ {path}/{col}_corr.xlsx")
                result_text = f"变量 {col} 相关性分析完成"
                script_body = "dc.relative_()"

            elif method == "supervised_":
                dc.supervised_()
                output_files += [f"  ✅ {path}/{col}_woe.xlsx", f"  ✅ {path}/{col}_woe.png"]
                result_text = f"变量 {col} 有监督分析完成"
                script_body = "dc.supervised_()"

            elif method == "easy_od":
                lower, upper, outlier_ratio = dc.easy_od(how=how)
                result_text = f"异常值检测: 下界={lower}, 上界={upper}, 异常比例={outlier_ratio:.4f}"
                script_body = f"lower, upper, ratio = dc.easy_od(how='{how}')\nprint(f'下界={{lower}}, 上界={{upper}}, 异常比例={{ratio:.4f}}')"

            elif method == "feat_card":
                dc.feat_card()
                output_files += [f"  ✅ {path}/{col}_*.xlsx/png (多张图表)"]
                result_text = f"变量 {col} 完整特征卡片生成完成"
                script_body = "dc.feat_card()"
            else:
                return ToolResult(success=False, output="", error=f"未知方法: {method}")

            save_script(path, f"run_{method}.py", f'''import pandas as pd
from cbhpacks.preprocess import desc_col
df = pd.read_csv("{csv_path}")
dc = desc_col(df=df, target="{target}", col="{col}", cols={cols},
    cat_cols={cat_cols}, corr_threshold={corr_threshold}, path="{path}")
{script_body}
print("分析完成，输出目录: {path}/")
''')
            output_files.append(f"  ✅ {path}/run_{method}.py — 可复现源码")

            return ToolResult(success=True, output=(
                f"📊 cbhpacks_desc_col.{method} 执行完成\n\n"
                f"📁 输出文件:\n" + "\n".join(output_files) + f"\n"
                f"  📁 输出目录: {path}/\n\n"
                f"📋 结果:\n{result_text}"
            ))
        except Exception as e:
            import traceback
            return ToolResult(success=False, output="", error=f"执行失败: {str(e)}\n{traceback.format_exc()}")
