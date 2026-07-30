"""cbhpacks 分箱工具 - bins_model 模块封装

会话级状态缓存（随 /new /reset 自动释放）+ 自动分目录 + 每次执行自动保存可复现的Python源码。
执行结果变量(bm/woe_data/iv_data等)自动注入 python 会话，可在 python 工具中直接使用。
"""
import os
import json
import numpy as np
import pandas as pd
from cbhcli_pkg.tools.registry import ToolResult
from cbhcli_pkg.tools.cbhpacks_session import CbhpacksSessionTool


def auto_new_path(base_path, cache, cache_key, attr_name="path"):
    used = any(k != cache_key and getattr(inst, attr_name, None) == base_path for k, inst in cache.items())
    if not used:
        return base_path
    v = 2
    while any(k != cache_key and getattr(inst, attr_name, None) == f"{base_path}_v{v}" for k, inst in cache.items()):
        v += 1
    return f"{base_path}_v{v}"


def save_script(output_dir, filename, code):
    """保存可复现的Python源码脚本"""
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, filename), 'w', encoding='utf-8') as f:
        f.write(code)


class BinsModelTool(CbhpacksSessionTool):

    @property
    def name(self): return "cbhpacks_bins_model"

    @property
    def description(self):
        return (
            "cbhpacks 分箱模型工具 - 分箱、WOE转换、IV计算、PSI稳定性检验。\n"
            "同一数据集多次调用共享状态（会话级缓存，/new /reset 后自动释放）。不同分箱类型自动分目录。\n"
            "每次执行自动保存可复现的Python源码脚本到输出目录。\n"
            "执行后结果变量自动注入 python 会话，可在 python 工具中直接使用：\n"
            "  bm(bins_model实例)/df/woe_data/iv_data/woe_df/woe_mapping/psi_data/psi_avg_data\n\n"
            "【method】comp_woe_iv/bins_rpt/data_to_woe/get_psi/psi_mth_avg/plot_col_rpt/plot_cols_rpt\n\n"
            "【⚠️ 注意事项】\n"
            "1. WOE转换必须使用本工具的data_to_woe，禁止使用cbhpacks_cols_encode.data_to_woe，"
            "因为本工具与comp_woe_iv共享分箱状态，确保一致性。\n"
            "2. data_to_woe不会自动继承comp_woe_iv的参数(group/bins_type/adj_bin等)，"
            "调用时必须手动传入与comp_woe_iv完全一致的参数，否则会用默认参数重新分箱。"
        )

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["comp_woe_iv", "bins_rpt", "data_to_woe", "get_psi", "psi_mth_avg", "plot_col_rpt", "plot_cols_rpt"]},
                "csv_path": {"type": "string"},
                "cols": {"type": "string", "description": "JSON格式"},
                "col": {"type": "string"},
                "target": {"type": "string", "default": "target"},
                "group": {"type": "integer", "default": 10},
                "nan": {"type": "number", "default": -999},
                "bins_type": {"type": "string", "enum": ["eq_cnt", "eq_distance", "deci_tree_bin", "chi2_bin", "cat_bin"], "default": "eq_cnt"},
                "adj_bin": {"type": "boolean", "default": False},
                "cat_cols": {"type": "boolean", "default": False},
                "min_group": {"type": "integer", "default": 2},
                "chi2_initial_group": {"type": "integer", "default": 20},
                "mth_col": {"type": "string"},
                "base_mth": {"type": "integer"},
                "cmp_mth": {"type": "integer"},
                "output_path": {"type": "string", "default": "step2_bins_result"},
                "show": {"type": "boolean", "default": False}
            },
            "required": ["method", "csv_path", "cols"]
        }

    def execute(self, **kwargs):
        try:
            method = kwargs.get("method")
            csv_path = kwargs.get("csv_path")
            cols_str = kwargs.get("cols")
            target = kwargs.get("target", "target")
            group = kwargs.get("group", 10)
            nan = kwargs.get("nan", -999)
            bins_type = kwargs.get("bins_type", "eq_cnt")
            adj_bin = kwargs.get("adj_bin", False)
            cat_cols = kwargs.get("cat_cols", False)
            min_group = kwargs.get("min_group", 2)
            chi2_initial_group = kwargs.get("chi2_initial_group", 20)
            mth_col = kwargs.get("mth_col")
            base_mth = kwargs.get("base_mth")
            cmp_mth = kwargs.get("cmp_mth")
            output_path = kwargs.get("output_path", "step2_bins_result")
            col = kwargs.get("col")
            show = kwargs.get("show", False)

            cols = json.loads(cols_str) if isinstance(cols_str, str) else cols_str
            if output_path == "step2_bins_result":
                output_path = f"step2_bins_result/{bins_type}"
            if not os.path.exists(csv_path):
                return ToolResult(success=False, output="", error=f"文件不存在: {csv_path}")

            from cbhpacks.bins_model import bins_model

            cache = self._get_cache('bins_model')
            cache_key = (csv_path, target, bins_type, group, tuple(sorted(cols)))
            bm = cache.get(cache_key)
            new_instance = bm is None

            if bm is None:
                output_path = auto_new_path(output_path, cache, cache_key, "path")
                df = pd.read_csv(csv_path)
                bm = bins_model(
                    df=df, cols=cols, group=group, target=target, nan=nan,
                    bins_type=bins_type, col=col, mth_col=mth_col,
                    base_mth=base_mth, cmp_mth=cmp_mth,
                    chi2_initial_group=chi2_initial_group, adj_bin=adj_bin,
                    cat_cols=cat_cols, min_group=min_group, path=output_path
                )
                cache[cache_key] = bm
            else:
                output_path = bm.path
                if col: bm.col = col
                if mth_col: bm.mth_col = mth_col
                if base_mth: bm.base_mth = base_mth
                if cmp_mth: bm.cmp_mth = cmp_mth

            output_files = []
            result_text = ""
            script_code = ""
            exposed = {'bm': bm, 'df': bm.df}  # 注入 python 会话的变量

            if method == "comp_woe_iv":
                woe_data, iv_data = bm.comp_woe_iv()
                # IV inf 防御（兼容旧版 cbhpacks 库）
                iv_data['iv_value'] = iv_data['iv_value'].replace([np.inf, -np.inf], 0)
                bm.cols_bins_rpt = woe_data
                bm.cols_iv_data = iv_data
                iv_data.to_csv(os.path.join(output_path, "iv_data.csv"), index=False)
                woe_data.to_csv(os.path.join(output_path, "woe_data.csv"), index=False)
                exposed.update(woe_data=woe_data, iv_data=iv_data)
                output_files += [f"  ✅ {output_path}/iv_data.csv", f"  ✅ {output_path}/woe_data.csv"]
                result_text = f"iv_data:\n{iv_data.to_string()}\n\nwoe_data前5行:\n{woe_data.head().to_string()}"
                script_code = f'''import pandas as pd
from cbhpacks.bins_model import bins_model

df = pd.read_csv("{csv_path}")
bm = bins_model(df=df, cols={cols}, group={group}, target="{target}", nan={nan},
    bins_type="{bins_type}", adj_bin={adj_bin}, cat_cols={cat_cols},
    min_group={min_group}, chi2_initial_group={chi2_initial_group}, path="{output_path}")
woe_data, iv_data = bm.comp_woe_iv()
iv_data.to_csv("{output_path}/iv_data.csv", index=False)
woe_data.to_csv("{output_path}/woe_data.csv", index=False)
print(f"iv_data shape: {{iv_data.shape}}")
print(iv_data)
'''

            elif method == "bins_rpt":
                if not col: return ToolResult(success=False, output="", error="bins_rpt需要指定col参数")
                bm.col = col
                woe_final, iv = bm.bins_rpt()
                # IV inf 防御（兼容旧版 cbhpacks 库）
                if iv == np.inf or iv == -np.inf:
                    iv = 0
                result_text = f"变量 {col} 分箱报告 (IV={iv}):\n{woe_final.to_string()}"
                exposed.update(woe_final=woe_final, iv=iv)
                script_code = f'''import pandas as pd
from cbhpacks.bins_model import bins_model

df = pd.read_csv("{csv_path}")
bm = bins_model(df=df, cols={cols}, group={group}, target="{target}", nan={nan},
    bins_type="{bins_type}", col="{col}", path="{output_path}")
woe_final, iv = bm.bins_rpt()
print(f"IV: {{iv}}")
print(woe_final)
'''

            elif method == "data_to_woe":
                woedf, woe_mapping = bm.data_to_woe()
                woedf.to_csv(os.path.join(output_path, "woe_transformed_data.csv"), index=False)
                exposed.update(woe_df=woedf, woe_mapping=woe_mapping)
                output_files += [f"  ✅ {output_path}/woe_mapping_*.pkl", f"  ✅ {output_path}/woe_transformed_data.csv"]
                result_text = f"WOE转换完成，{len(woe_mapping)} 个变量"
                script_code = f'''import pandas as pd
from cbhpacks.bins_model import bins_model

df = pd.read_csv("{csv_path}")
bm = bins_model(df=df, cols={cols}, group={group}, target="{target}", nan={nan},
    bins_type="{bins_type}", path="{output_path}")
woe_data, iv_data = bm.comp_woe_iv()
woedf, woe_mapping = bm.data_to_woe()
woedf.to_csv("{output_path}/woe_transformed_data.csv", index=False)
print(f"WOE转换完成，{{len(woe_mapping)}} 个变量")
'''

            elif method == "get_psi":
                if not mth_col or not base_mth or not cmp_mth:
                    return ToolResult(success=False, output="", error="get_psi需要指定mth_col/base_mth/cmp_mth")
                bm.mth_col, bm.base_mth, bm.cmp_mth = mth_col, base_mth, cmp_mth
                psi_data = bm.get_psi()
                exposed.update(psi_data=psi_data)
                output_files.append(f"  ✅ {output_path}/psi_single_rpt_*.xlsx")
                result_text = f"PSI数据:\n{psi_data.to_string()}"
                script_code = f'''import pandas as pd
from cbhpacks.bins_model import bins_model

df = pd.read_csv("{csv_path}")
bm = bins_model(df=df, cols={cols}, group={group}, target="{target}", nan={nan},
    bins_type="{bins_type}", mth_col="{mth_col}", base_mth={base_mth}, cmp_mth={cmp_mth}, path="{output_path}")
psi_data = bm.get_psi()
print(psi_data)
'''

            elif method == "psi_mth_avg":
                if not mth_col or not base_mth:
                    return ToolResult(success=False, output="", error="psi_mth_avg需要指定mth_col/base_mth")
                bm.mth_col, bm.base_mth = mth_col, base_mth
                psi_avg_data = bm.psi_mth_avg()
                exposed.update(psi_avg_data=psi_avg_data)
                output_files.append(f"  ✅ {output_path}/psi_avg_rpt_*.xlsx")
                result_text = f"PSI月均值:\n{psi_avg_data.to_string()}"
                script_code = f'''import pandas as pd
from cbhpacks.bins_model import bins_model

df = pd.read_csv("{csv_path}")
bm = bins_model(df=df, cols={cols}, group={group}, target="{target}", nan={nan},
    bins_type="{bins_type}", mth_col="{mth_col}", base_mth={base_mth}, path="{output_path}")
psi_avg_data = bm.psi_mth_avg()
print(psi_avg_data)
'''

            elif method == "plot_col_rpt":
                if not col: return ToolResult(success=False, output="", error="plot_col_rpt需要指定col参数")
                bm.col = col
                bm.plot_col_rpt(show=show)
                output_files.append(f"  ✅ {output_path}/{col}_*.png (4张图)")
                result_text = f"变量 {col} 的分箱可视化图表已生成"
                script_code = f'''import pandas as pd
from cbhpacks.bins_model import bins_model

df = pd.read_csv("{csv_path}")
bm = bins_model(df=df, cols={cols}, group={group}, target="{target}", nan={nan},
    bins_type="{bins_type}", col="{col}", path="{output_path}")
bm.plot_col_rpt(show=False)
print("图表已保存到 {output_path}/")
'''

            elif method == "plot_cols_rpt":
                bm.plot_cols_rpt(show=show)
                output_files.append(f"  ✅ {output_path}/ 各变量分箱图表")
                result_text = f"共 {len(cols)} 个变量的分箱图表已生成"
                script_code = f'''import pandas as pd
from cbhpacks.bins_model import bins_model

df = pd.read_csv("{csv_path}")
bm = bins_model(df=df, cols={cols}, group={group}, target="{target}", nan={nan},
    bins_type="{bins_type}", path="{output_path}")
bm.plot_cols_rpt(show=False)
print("图表已保存到 {output_path}/")
'''
            else:
                return ToolResult(success=False, output="", error=f"未知方法: {method}")

            # 结果变量注入 python 会话（bm/df/woe_data/iv_data 等，python 工具可直接使用）
            self._expose(**exposed)

            # 保存可复现源码
            if script_code:
                save_script(output_path, f"run_{method}.py", script_code)
                output_files.append(f"  ✅ {output_path}/run_{method}.py — 可复现源码")

            output = (
                f"📊 cbhpacks_bins_model.{method} 执行完成\n\n"
                f"📁 输出文件:\n" + "\n".join(output_files) + "\n\n"
                f"📋 结果:\n{result_text}\n\n"
                f"💡 已注入 python 会话变量: {', '.join(exposed.keys())}"
            )
            return ToolResult(success=True, output=output)

        except Exception as e:
            import traceback
            return ToolResult(success=False, output="", error=f"执行失败: {str(e)}\n{traceback.format_exc()}")
