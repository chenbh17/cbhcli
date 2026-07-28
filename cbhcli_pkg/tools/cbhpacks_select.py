"""cbhpacks 特征选择工具 - cols_select 模块封装

会话级状态缓存（随 /new /reset 自动释放）+ 自动分目录 + 每次执行自动保存可复现的Python源码。
执行结果变量(cs/selected_cols等)自动注入 python 会话，可在 python 工具中直接使用。
"""
import os
import json
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
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, filename), 'w', encoding='utf-8') as f:
        f.write(code)


class ColsSelectTool(CbhpacksSessionTool):

    @property
    def name(self): return "cbhpacks_cols_select"

    @property
    def description(self):
        return (
            "cbhpacks 通用特征筛选工具 - 10种筛选方法逐步筛选特征。\n"
            "同一数据集多次调用共享状态（会话级缓存，/new /reset 后自动释放），self.cols_s 会逐步缩减。\n"
            "每次执行自动保存可复现的Python源码脚本到输出目录。\n"
            "执行后结果变量自动注入 python 会话：cs(cols_select实例)/df/selected_cols\n\n"
            "【method】null_select/enumerate_select/iv_select/psi_select/corr_select/chi2_select/logistic_select/ml_select/boostrap_select/vif_select\n\n"
            "【⚠️ 依赖数据说明】\n"
            "- iv_data_csv: iv_select和corr_select必须传入，来自cbhpacks_bins_model.comp_woe_iv产出的iv_data_xxx.csv\n"
            "- psi_data_csv: psi_select必须传入，来自cbhpacks_bins_model.get_psi产出的psi_data_xxx.csv\n"
            "若不传入这些参数，对应筛选方法会报错。"
        )

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["null_select", "enumerate_select", "iv_select", "psi_select", "corr_select", "chi2_select", "logistic_select", "ml_select", "boostrap_select", "vif_select"]},
                "csv_path": {"type": "string"},
                "cols": {"type": "string", "description": "JSON格式"},
                "target": {"type": "string", "default": "target"},
                "path": {"type": "string", "default": "step4_cols_select"},
                "iv_data_csv": {"type": "string"},
                "psi_data_csv": {"type": "string"},
                "null_pct": {"type": "number", "default": 0.95},
                "enu_cnt": {"type": "integer", "default": 1},
                "enu_pct": {"type": "number", "default": 0.95},
                "iv_thres": {"type": "number", "default": 0.01},
                "psi_thres": {"type": "number", "default": 0.1},
                "corr_thres": {"type": "number", "default": 0.8},
                "corr_method": {"type": "string", "enum": ["spearman", "pearson", "kendall"], "default": "spearman"},
                "chi2_p_value_thres": {"type": "number", "default": 0.5},
                "lg_method": {"type": "string", "enum": ["recursion", "l1penalty"], "default": "recursion"},
                "lg_C": {"type": "number", "default": 0.1},
                "ml_method": {"type": "string", "enum": ["lgb", "xgb", "rdf"], "default": "lgb"},
                "boot_method": {"type": "string", "enum": ["lgb", "xgb", "rdf"], "default": "lgb"},
                "boot_thres": {"type": "number", "default": 100},
                "vif_thres": {"type": "number", "default": 10},
                "nan": {"type": "number", "default": 0},
                "reset": {"type": "boolean", "default": False}
            },
            "required": ["method", "csv_path", "cols"]
        }

    def execute(self, **kwargs):
        try:
            method = kwargs.get("method")
            csv_path = kwargs.get("csv_path")
            cols_str = kwargs.get("cols")
            target = kwargs.get("target", "target")
            path = kwargs.get("path", "step4_cols_select")
            iv_data_csv = kwargs.get("iv_data_csv")
            psi_data_csv = kwargs.get("psi_data_csv")
            null_pct = kwargs.get("null_pct", 0.95)
            enu_cnt = kwargs.get("enu_cnt", 1)
            enu_pct = kwargs.get("enu_pct", 0.95)
            iv_thres = kwargs.get("iv_thres", 0.01)
            psi_thres = kwargs.get("psi_thres", 0.1)
            corr_thres = kwargs.get("corr_thres", 0.8)
            corr_method = kwargs.get("corr_method", "spearman")
            chi2_p_value_thres = kwargs.get("chi2_p_value_thres", 0.5)
            lg_method = kwargs.get("lg_method", "recursion")
            lg_C = kwargs.get("lg_C", 0.1)
            ml_method = kwargs.get("ml_method", "lgb")
            boot_method = kwargs.get("boot_method", "lgb")
            boot_thres = kwargs.get("boot_thres", 100)
            vif_thres = kwargs.get("vif_thres", 10)
            nan = kwargs.get("nan", 0)
            reset = kwargs.get("reset", False)

            cols = json.loads(cols_str) if isinstance(cols_str, str) else cols_str
            if not os.path.exists(csv_path):
                return ToolResult(success=False, output="", error=f"文件不存在: {csv_path}")

            from cbhpacks.cols_select import cols_select

            cache = self._get_cache('cols_select')
            cache_key = (csv_path, target)
            cs = cache.get(cache_key)

            if cs is None or reset:
                path = auto_new_path(path, cache, cache_key, "path")
                df = pd.read_csv(csv_path)
                iv_data = pd.read_csv(iv_data_csv) if iv_data_csv and os.path.exists(iv_data_csv) else None
                psi_data = pd.read_csv(psi_data_csv) if psi_data_csv and os.path.exists(psi_data_csv) else None

                cs = cols_select(
                    df=df, cols=cols, target=target, psi_data=psi_data, iv_data=iv_data,
                    null_pct=null_pct, enu_cnt=enu_cnt, enu_pct=enu_pct,
                    psi_thres=psi_thres, iv_thres=iv_thres, corr_method=corr_method, corr_thres=corr_thres,
                    chi2_p_value_thres=chi2_p_value_thres, lg_method=lg_method, lg_C=lg_C,
                    ml_method=ml_method, boot_method=boot_method, boot_thres=boot_thres,
                    vif_thres=vif_thres, nan=nan, path=path
                )
                cache[cache_key] = cs
            else:
                path = cs.path
                if iv_data_csv and os.path.exists(iv_data_csv): cs.iv_data = pd.read_csv(iv_data_csv)
                if psi_data_csv and os.path.exists(psi_data_csv): cs.psi_data = pd.read_csv(psi_data_csv)

            method_map = {
                "null_select": cs.null_select, "enumerate_select": cs.enumerate_select,
                "iv_select": cs.iv_select, "psi_select": cs.psi_select,
                "corr_select": cs.corr_select, "chi2_select": cs.chi2_select,
                "logistic_select": cs.logistic_select, "ml_select": cs.ml_select,
                "boostrap_select": cs.boostrap_select, "vif_select": cs.vif_select,
            }
            if method not in method_map:
                return ToolResult(success=False, output="", error=f"未知方法: {method}")

            result = method_map[method]()
            selected_cols = cs.cols_s
            original_count = len(cols)

            # 结果变量注入 python 会话
            self._expose(cs=cs, df=cs.df, selected_cols=selected_cols)

            # 保存筛选结果JSON
            os.makedirs(path, exist_ok=True)
            with open(os.path.join(path, f"{method}_selected_cols.json"), 'w') as f:
                json.dump(selected_cols, f)

            # 生成可复现源码
            script_code = f'''import pandas as pd
from cbhpacks.cols_select import cols_select

df = pd.read_csv("{csv_path}")
iv_data = pd.read_csv("{iv_data_csv}") if "{iv_data_csv}" else None
psi_data = pd.read_csv("{psi_data_csv}") if "{psi_data_csv}" else None
cs = cols_select(df=df, cols={cols}, target="{target}", iv_data=iv_data, psi_data=psi_data,
    null_pct={null_pct}, enu_cnt={enu_cnt}, enu_pct={enu_pct}, iv_thres={iv_thres}, psi_thres={psi_thres},
    corr_method="{corr_method}", corr_thres={corr_thres}, chi2_p_value_thres={chi2_p_value_thres},
    lg_method="{lg_method}", lg_C={lg_C}, ml_method="{ml_method}", boot_method="{boot_method}",
    boot_thres={boot_thres}, vif_thres={vif_thres}, nan={nan}, path="{path}")
result = cs.{method}()
print(f"筛选前: {{len(cs.cols)}} 个特征")
print(f"筛选后: {{len(cs.cols_s)}} 个特征: {{cs.cols_s}}")
'''
            save_script(path, f"run_{method}.py", script_code)

            output_files = [
                f"  ✅ 筛选前特征数: {original_count}",
                f"  ✅ 筛选后特征数: {len(selected_cols)}",
                f"  ✅ 筛选后特征: {selected_cols}",
                f"  ✅ {path}/{method}_selected_cols.json",
                f"  ✅ {path}/run_{method}.py — 可复现源码",
            ]

            result_text = f"方法: {method}\n筛选前: {original_count} → 筛选后: {len(selected_cols)}\n特征: {selected_cols}"
            if hasattr(result, 'to_string'):
                result_text += f"\n\n筛选详情:\n{result.to_string()}"

            return ToolResult(success=True, output=(
                f"📊 cbhpacks_cols_select.{method} 执行完成\n\n"
                f"📁 输出文件:\n" + "\n".join(output_files) + f"\n"
                f"  📁 输出目录: {path}/\n\n"
                f"📋 结果:\n{result_text}\n\n"
                f"💡 selected_cols 可直接作为 cbhpacks_binary_model 的 cols 参数\n"
                f"💡 已注入 python 会话变量: cs, df, selected_cols"
            ))

        except Exception as e:
            import traceback
            return ToolResult(success=False, output="", error=f"执行失败: {str(e)}\n{traceback.format_exc()}")


class ColsSelectJsTool(CbhpacksSessionTool):
    @property
    def name(self): return "cbhpacks_cols_select_js"

    @property
    def description(self):
        return ("cbhpacks 递归特征筛选工具 - 递归迭代剔除低重要性特征。每次执行自动保存可复现源码。\n"
                "执行后结果变量自动注入 python 会话：cs_js/js_data/cols_detail/js_cols/train/test")

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["recursion_select"], "default": "recursion_select"},
                "train_csv": {"type": "string"},
                "test_csv": {"type": "string"},
                "cols": {"type": "string", "description": "JSON格式"},
                "target": {"type": "string", "default": "target"},
                "method_type": {"type": "string", "enum": ["xgb", "lgb"], "default": "lgb"},
                "recursion_num": {"type": "integer", "default": 30},
                "stay_pct": {"type": "number", "default": 0.95},
                "path": {"type": "string", "default": "step5_cols_js"}
            },
            "required": ["train_csv", "test_csv", "cols"]
        }

    def execute(self, **kwargs):
        try:
            train_csv = kwargs.get("train_csv")
            test_csv = kwargs.get("test_csv")
            cols = json.loads(kwargs.get("cols")) if isinstance(kwargs.get("cols"), str) else kwargs.get("cols")
            target = kwargs.get("target", "target")
            method_type = kwargs.get("method_type", "lgb")
            recursion_num = kwargs.get("recursion_num", 30)
            stay_pct = kwargs.get("stay_pct", 0.95)
            path = kwargs.get("path", "step5_cols_js")

            if not os.path.exists(train_csv):
                return ToolResult(success=False, output="", error=f"训练集文件不存在: {train_csv}")
            if not os.path.exists(test_csv):
                return ToolResult(success=False, output="", error=f"测试集文件不存在: {test_csv}")

            train = pd.read_csv(train_csv)
            test = pd.read_csv(test_csv)

            from cbhpacks.cols_select import cols_select_js
            cs = cols_select_js(train=train, test=test, cols=cols, target=target,
                               method=method_type, recursion_num=recursion_num, stay_pct=stay_pct, path=path)
            js_data, cols_detail, js_cols = cs.recursion_select()

            # 结果变量注入 python 会话
            self._expose(cs_js=cs, js_data=js_data, cols_detail=cols_detail, js_cols=js_cols,
                         train=train, test=test)

            script_code = f'''import pandas as pd
from cbhpacks.cols_select import cols_select_js

train = pd.read_csv("{train_csv}")
test = pd.read_csv("{test_csv}")
cs = cols_select_js(train=train, test=test, cols={cols}, target="{target}",
    method="{method_type}", recursion_num={recursion_num}, stay_pct={stay_pct}, path="{path}")
js_data, cols_detail, js_cols = cs.recursion_select()
print(f"最优特征组合 ({{len(js_cols)}}个): {{js_cols}}")
'''
            save_script(path, "run_recursion_select.py", script_code)

            return ToolResult(success=True, output=(
                f"📊 cbhpacks_cols_select_js.recursion_select 执行完成\n\n"
                f"📁 输出文件:\n"
                f"  ✅ {path}/ — 递归筛选图表和报告\n"
                f"  ✅ {path}/run_recursion_select.py — 可复现源码\n\n"
                f"📋 最优特征组合({len(js_cols)}个): {js_cols}\n\n"
                f"💡 js_cols 可直接作为 cbhpacks_binary_model 的 cols 参数\n"
                f"💡 已注入 python 会话变量: cs_js, js_data, cols_detail, js_cols, train, test"
            ))
        except Exception as e:
            import traceback
            return ToolResult(success=False, output="", error=f"执行失败: {str(e)}\n{traceback.format_exc()}")
