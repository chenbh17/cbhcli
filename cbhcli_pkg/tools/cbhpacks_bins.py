"""cbhpacks 分箱工具 - bins_model 模块封装

会话级状态缓存（随 /new /reset 自动释放）+ 自动分目录 + 每次执行自动保存可复现的Python源码。
执行结果变量(bm/woe_data/iv_data等)自动注入 python 会话，可在 python 工具中直接使用。
v5.3.1 护栏：comp_woe_iv 内置修正IV+原始数据相关双层泄漏检测；data_to_woe 支持 output_csv；
get_psi 月份预校验。
"""
import os
import json
import numpy as np
import pandas as pd
from cbhcli_pkg.tools.registry import ToolResult
from cbhcli_pkg.tools.cbhpacks_session import CbhpacksSessionTool
from cbhcli_pkg.tools.cbhpacks_guard import (
    check_missing_fill, check_bins_quality, check_bin_inf_vars,
    check_psi, check_leakage_from_woe, check_target_leakage_raw,
    format_findings, Finding,
)


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
            "2. data_to_woe 分箱参数继承规则：完全省略分箱参数(group/adj_bin/min_group/cat_cols)时"
            "自动继承同数据集最近一次comp_woe_iv的参数（推荐做法）；"
            "显式传参与既有分箱不一致会触发C2警告（建议去掉参数让其继承）。\n"
            "3. data_to_woe 可传 output_csv 指定另存路径（如 step3_woe/train_woe.csv），"
            "下游训练直接用该路径作 train_csv。\n"
            "4. get_psi/psi_mth_avg 的 base_mth/cmp_mth 必须真实存在于数据月份列中"
            "（用全量数据计算；报错会列出可用月份）。"
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
                "output_csv": {"type": "string", "description": "data_to_woe 专用：WOE转换结果另存路径（如 step3_woe/train_woe.csv，下游训练直接作 train_csv）。不传则默认 output_path/woe_transformed_data.csv"},
                "woe_mapping_pkl": {"type": "string", "description": "训练集产出的 woe_mapping_<bins_type>.pkl 路径。data_to_woe 对测试集/OOT 转换时传入（防数据穿越，用训练集映射不重新分箱）"},
                "show": {"type": "boolean", "default": False}
            },
            "required": ["method", "csv_path", "cols"]
        }

    def execute(self, **kwargs):
        self._findings_buffer = []  # 清空上次残留（异常路径可能未取走）
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

            param_mismatch_note = None
            explicit_params = {k: kwargs[k] for k in ("group", "adj_bin", "min_group", "cat_cols")
                               if k in kwargs}
            # ═══ Harness: data_to_woe 分箱参数自动继承 ═══
            # 未显式传分箱参数时，从会话缓存中继承同数据集最近一次 comp_woe_iv 的参数，
            # 避免因参数不一致导致 cache miss 新建实例、用默认参数重新分箱（分箱/转换错位）。
            if method == "data_to_woe" and not explicit_params:
                for _k, _inst in cache.items():
                    if len(_k) >= 7 and _k[0] == csv_path and _k[1] == target \
                            and _k[2] == bins_type:
                        group = _k[3]
                        adj_bin = _k[4]
                        min_group = _k[5]
                        cat_cols = _k[6]
                        break
            # ═══ 修复(v5.3.1) Bug 11: data_to_woe 显式传参但与缓存分箱参数不一致 → 警告 ═══
            # 参数继承只在完全省略时生效；显式传错参数会静默 cache miss 重新分箱（错位）
            if method == "data_to_woe" and explicit_params:
                for _k, _inst in cache.items():
                    if len(_k) >= 7 and _k[0] == csv_path and _k[1] == target \
                            and _k[2] == bins_type:
                        cached = {"group": _k[3], "adj_bin": _k[4],
                                  "min_group": _k[5], "cat_cols": _k[6]}
                        mismatched = {k: f"{explicit_params[k]}(传入)≠{cached[k]}(分箱)" 
                                      for k in explicit_params
                                      if k in cached and explicit_params[k] != cached[k]}
                        if mismatched:
                            param_mismatch_note = Finding(
                                "WARN", "C2",
                                f"data_to_woe 显式传入的分箱参数与既有 comp_woe_iv 分箱不一致: {mismatched}",
                                "参数不一致将导致 cache miss、用传入参数重新分箱（分箱/转换错位）。"
                                "建议去掉这些参数由工具自动继承，或改为与 comp_woe_iv 完全一致")
                        break

            # 完整分箱参数作为缓存 key（参数不同 → 不同实例，语义正确）
            cache_key = (csv_path, target, bins_type, group, adj_bin, min_group,
                         cat_cols, tuple(sorted(cols)))
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
                # ═══ Harness 检查: 分箱质量 + 缺失填充 + 目标泄漏 ═══
                # IV=inf 先提取警告清单再置 0（不再静默掩盖分箱问题）
                findings = check_bin_inf_vars(iv_data)
                iv_data['iv_value'] = iv_data['iv_value'].replace([np.inf, -np.inf], 0)
                findings += check_bins_quality(woe_data, n_samples=len(bm.df))
                findings += check_missing_fill(bm.df, cols, nan=nan)
                # 修复(v5.3.1) 问题3: 内置 C5 修正IV检查——完美泄漏特征
                # （如 target*10）经 adj_bin 后报告 IV 被置 0 洗白，
                # 从 woe_data 分箱明细重算修正 IV 兜底报警
                c5_findings, c5_corrected = check_leakage_from_woe(woe_data)
                findings += c5_findings
                # 修复(v5.3.1) 问题3终极兜底: 原始数据级相关检测——
                # 极端参数下（如 group 过小）泄漏特征可能被 adj_bin 合并为单箱，
                # 分箱明细也失效，此时用原始数据点二列相关（|r|>0.9）检测
                findings += check_target_leakage_raw(bm.df, cols, target, corr_thres=0.9)
                self._expose(corrected_iv=c5_corrected)
                harness_note = format_findings(findings)
                self._log_findings(findings)
                bm.cols_bins_rpt = woe_data
                bm.cols_iv_data = iv_data
                iv_data.to_csv(os.path.join(output_path, "iv_data.csv"), index=False)
                woe_data.to_csv(os.path.join(output_path, "woe_data.csv"), index=False)
                exposed.update(woe_data=woe_data, iv_data=iv_data)
                output_files += [f"  ✅ {output_path}/iv_data.csv", f"  ✅ {output_path}/woe_data.csv"]
                result_text = f"iv_data:\n{iv_data.to_string()}\n\nwoe_data前5行:\n{woe_data.head().to_string()}"
                if harness_note:
                    result_text += f"\n\n{harness_note}"
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
                # IV inf 防御（兼容旧版 cbhpacks 库）——修复(v5.3.1) Bug 14：置 0 时显式说明。
                # 注：当前版 cbhpacks 库 bins_rpt 对 WOE=inf 的箱直接把 woe/iv_bin 置 0（IV 亦为 0），
                # 此防御仅旧版库 inf 直传时生效；无论哪条路径，B1 分箱质量检查都会兜底报告单好/单坏箱。
                findings = []
                if iv == np.inf or iv == -np.inf:
                    iv = 0
                    findings.append(Finding(
                        "WARN", "B1",
                        f"变量 {col} 分箱存在单好/单坏箱，IV 为无穷大已置 0",
                        "IV 被严重低估。建议 adj_bin=True 合并相邻箱或调大 min_group 重新分箱"))
                elif iv == 0 and not woe_final.empty:
                    # 修复(v5.3.1)：库把含 inf 箱的 woe/iv_bin 置 0 → 整体 IV=0 伪装成"无区分度"，
                    # 与真实无区分（各箱 bad_rate 相同）不可区分——补一条 INFO 说明真实成因
                    has_single_side_bin = bool(
                        ((woe_final.get("bad_cnt") == 0) | (woe_final.get("good_cnt") == 0)).any())
                    if has_single_side_bin:
                        findings.append(Finding(
                            "WARN", "B1",
                            f"变量 {col} 存在单好/单坏箱，库已将 WOE/IV 置 0（报告 IV={iv} 不代表无区分度，"
                            f"实际该变量区分度被低估）",
                            "建议 adj_bin=True 合并相邻箱或调大 min_group 重新分箱"))
                # Harness: 单变量分箱质量检查
                findings += check_bins_quality(woe_final, n_samples=len(bm.df))
                harness_note = format_findings(findings)
                self._log_findings(findings)
                result_text = f"变量 {col} 分箱报告 (IV={iv}):\n{woe_final.to_string()}"
                if harness_note:
                    result_text += f"\n\n{harness_note}"
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
                # ═══ Harness: 跨数据集泄漏防护（WOE 只在训练集计算铁律）═══
                woe_mapping_pkl = kwargs.get("woe_mapping_pkl")
                # 是否存在同数据集（csv_path+target+bins_type）的既有分箱记录：
                # ① 命中缓存实例（new_instance=False）说明同数据集同参数已有分箱；
                # ② 或 cache 中存在同数据集的其他参数实例（排除 cache_key 自身防误判）
                has_existing_bins = (not new_instance) or any(
                    len(_k) >= 7 and _k != cache_key
                    and _k[0] == csv_path and _k[1] == target
                    and _k[2] == bins_type for _k in cache)
                findings = []
                if param_mismatch_note is not None:
                    findings.append(param_mismatch_note)  # 修复(v5.3.1) Bug 11
                if woe_mapping_pkl:
                    # 用训练集映射对当前数据集（如测试集/OOT）转换，不重新分箱
                    if not os.path.exists(woe_mapping_pkl):
                        return ToolResult(success=False, output="",
                                          error=f"woe_mapping_pkl 文件不存在: {woe_mapping_pkl}")
                    import joblib as _joblib
                    _mapping = _joblib.load(woe_mapping_pkl)
                    woedf, woe_mapping = bm.apply_woe(bm.df, woe_mapping=_mapping)
                    map_src = f"训练集映射 {os.path.basename(woe_mapping_pkl)}"
                else:
                    if not has_existing_bins:
                        # C2 泄漏防护：无既有分箱记录的数据集做 data_to_woe 会基于自身重新分箱
                        findings.append(Finding(
                            "WARN", "C2",
                            f"data_to_woe 对数据集 {os.path.basename(csv_path)} 无既有分箱记录，"
                            f"将基于该数据集自身重新分箱计算 WOE（违反'WOE 只在训练集计算'铁律，存在数据穿越）",
                            "正确做法：先对训练集 comp_woe_iv 分箱，再用 woe_mapping_pkl 参数"
                            "（训练集产出的 woe_mapping_*.pkl）对测试集/OOT 转换"))
                    woedf, woe_mapping = bm.data_to_woe()
                    map_src = f"{bins_type} 分箱"
                woedf.to_csv(os.path.join(output_path, "woe_transformed_data.csv"), index=False)
                exposed.update(woe_df=woedf, woe_mapping=woe_mapping)
                output_files += [f"  ✅ {output_path}/woe_mapping_*.pkl", f"  ✅ {output_path}/woe_transformed_data.csv"]
                result_text = f"WOE转换完成，{len(woe_mapping)} 个变量（{map_src}）"
                # 修复(v5.3.1) 问题4: output_csv 参数生效——
                # 原实现 schema 中无此参数、固定输出 woe_transformed_data.csv，
                # AI 按习惯传 output_csv 时不生效，下游按该路径喂模型报"需要提供train_csv"
                output_csv = kwargs.get("output_csv")
                if output_csv:
                    os.makedirs(os.path.dirname(output_csv) or ".", exist_ok=True)
                    woedf.to_csv(output_csv, index=False)
                    output_files.append(f"  ✅ {output_csv}（output_csv 参数指定）")
                    result_text += f"\n→ 已按 output_csv 保存: {output_csv}（下游训练直接用此路径作 train_csv）"
                harness_note = format_findings(findings)
                self._log_findings(findings)
                if harness_note:
                    result_text += f"\n\n{harness_note}"
                script_code = f'''import pandas as pd
from cbhpacks.bins_model import bins_model

df = pd.read_csv("{csv_path}")
bm = bins_model(df=df, cols={cols}, group={group}, target="{target}", nan={nan},
    bins_type="{bins_type}", path="{output_path}")
woe_data, iv_data = bm.comp_woe_iv()
woedf, woe_mapping = bm.data_to_woe()
woedf.to_csv("{output_path}/woe_transformed_data.csv", index=False)
{f'woedf.to_csv("{output_csv}", index=False)  # output_csv 参数指定' if kwargs.get("output_csv") else ''}
print(f"WOE转换完成，{{len(woe_mapping)}} 个变量")
'''

            elif method == "get_psi":
                if not mth_col or not base_mth or not cmp_mth:
                    return ToolResult(success=False, output="", error="get_psi需要指定mth_col/base_mth/cmp_mth")
                # 修复(v5.3.1) 问题5: cmp_mth/base_mth 必须真实存在于数据月份中——
                # 原实现在库层 get_group 报裸 KeyError: 202408，无引导信息
                _mth_df = pd.read_csv(csv_path, usecols=[mth_col]) \
                    if mth_col in pd.read_csv(csv_path, nrows=0).columns else None
                if _mth_df is None:
                    return ToolResult(success=False, output="",
                        error=f"csv 数据中不存在月份列 {mth_col}，无法计算 PSI。"
                              f"请检查 mth_col 参数或数据文件")
                _mth_vals = set(pd.unique(_mth_df[mth_col].astype(int)))
                _missing_mth = [m for m in (base_mth, cmp_mth) if int(m) not in _mth_vals]
                if _missing_mth:
                    avail = sorted(_mth_vals)
                    return ToolResult(success=False, output="",
                        error=f"get_psi 的 cmp_mth/base_mth {sorted(_missing_mth)} 不在数据 {mth_col} 列中，"
                              f"可用月份: {avail}。"
                              f"请改用数据中存在的月份，或改用 psi_mth_avg 方法（自动遍历数据中全部月份"
                              f"计算各月相对 base_mth 的 PSI 均值）；"
                              f"另注意: PSI 对比应使用全量数据的月份列，用只含部分月份的训练集会报本错误")
                bm.mth_col, bm.base_mth, bm.cmp_mth = mth_col, base_mth, cmp_mth
                # 修复(v5.3.1): 库层 get_psi 循环中途把 self.df 替换为单月子集且异常路径不恢复，
                # 污染缓存实例（后续 comp_woe_iv 复用会静默用单月数据分箱）——
                # try/finally 恒恢复实例状态
                try:
                    psi_data = bm.get_psi()
                finally:
                    bm.df = bm.df_copy
                    bm.col = bm.col_copy
                exposed.update(psi_data=psi_data)
                output_files.append(f"  ✅ {output_path}/psi_single_rpt_*.xlsx")
                result_text = f"PSI数据:\n{psi_data.to_string()}"
                # 修复(v5.3.1) Bug 16: PSI 产出自动内置 E1 稳定性分级检查（与其他工具自动检查风格一致）
                findings = check_psi(psi_data)
                harness_note = format_findings(findings)
                self._log_findings(findings)
                if harness_note:
                    result_text += f"\n\n{harness_note}"
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
                # 修复(v5.3.1): base_mth 不在数据月份中时，库层 cmp_mth_list.remove() 报裸
                # ValueError: list.remove(x): x not in list——预校验给出可用月份
                _mth_df2 = pd.read_csv(csv_path, usecols=[mth_col]) \
                    if mth_col in pd.read_csv(csv_path, nrows=0).columns else None
                if _mth_df2 is None:
                    return ToolResult(success=False, output="",
                        error=f"csv 数据中不存在月份列 {mth_col}，无法计算 PSI。"
                              f"请检查 mth_col 参数或数据文件")
                _mth_vals2 = set(pd.unique(_mth_df2[mth_col].astype(int)))
                if int(base_mth) not in _mth_vals2:
                    return ToolResult(success=False, output="",
                        error=f"psi_mth_avg 的 base_mth={base_mth} 不在数据 {mth_col} 列中，"
                              f"可用月份: {sorted(_mth_vals2)}。请改用数据中存在的月份作为基准月")
                bm.mth_col, bm.base_mth = mth_col, base_mth
                # 修复(v5.3.1): 与 get_psi 相同的状态污染防护（try/finally 恒恢复实例状态）
                try:
                    psi_avg_data = bm.psi_mth_avg()
                finally:
                    bm.df = bm.df_copy
                    bm.col = bm.col_copy
                    bm.cmp_mth = bm.com_mth_copy
                exposed.update(psi_avg_data=psi_avg_data)
                output_files.append(f"  ✅ {output_path}/psi_avg_rpt_*.xlsx")
                result_text = f"PSI月均值:\n{psi_avg_data.to_string()}"
                # 修复(v5.3.1) Bug 16: PSI 产出自动内置 E1 稳定性分级检查
                findings = check_psi(psi_avg_data)
                harness_note = format_findings(findings)
                self._log_findings(findings)
                if harness_note:
                    result_text += f"\n\n{harness_note}"
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
            return ToolResult(success=True, output=output,
                              harness_findings=self._pop_findings())

        except Exception as e:
            import traceback
            return ToolResult(success=False, output="", error=f"执行失败: {str(e)}\n{traceback.format_exc()}")
