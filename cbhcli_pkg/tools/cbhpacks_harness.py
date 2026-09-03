"""cbhpacks Harness 校验工具 — 数据科学建模确定性检查（v5.3.1）

Harness Engineering 的"确定性策略引擎"层：把建模护栏做成显式工具，
AI 在建模流程关键节点（分箱后/筛选后/训练后/评估后）主动调用，
检查结果由代码计算、结构化返回，并写入审计日志（harness_check 事件）。

检查项分级：🟡 WARN（高风险+修复建议）/ 🔵 INFO（提示）。
结构类提示（A0/D0/E0）表示"该检查未实际执行"，防假阴性；
所有检查绝不抛异常、绝不修改数据——纯只读校验。
"""
import os
import json
import pandas as pd
from cbhcli_pkg.tools.registry import ToolResult
from cbhcli_pkg.tools.cbhpacks_session import CbhpacksSessionTool
from cbhcli_pkg.tools.cbhpacks_guard import (
    check_missing_fill, check_prefilled_suspicion, check_bins_quality,
    check_bin_inf_vars, check_time_split, check_target_leakage,
    check_leakage_from_woe, check_target_leakage_raw, check_overfit,
    check_confusion_schema, check_psi, check_psi_schema, HarnessReport, Finding,
)


def _read_table(path):
    """读取 Excel/CSV 表格（report 产出 xlsx，iv/woe 产出 csv）"""
    if not path or not os.path.exists(path):
        return None
    if path.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(path)
    return pd.read_csv(path)


class CbhpacksHarnessTool(CbhpacksSessionTool):

    @property
    def name(self): return "cbhpacks_harness"

    @property
    def description(self):
        return (
            "cbhpacks 建模护栏校验工具 - 数据科学建模确定性检查（Harness）。\n"
            "在建模流程关键节点调用，检查结果由规则代码计算（不依赖模型自觉），"
            "发现高风险时给出修复建议，但绝不修改数据、绝不阻断执行（纯只读校验）。\n\n"
            "【method】\n"
            "- check_data: 缺失填充合理性（需 csv_path+cols，可选 nan=填充值）。\n"
            "  ⚠️ 必须传原始含NaN数据+nan参数；若传已预填充(fillna)的数据+nan会触发A0提示，A1/A2无法评估\n"
            "- check_bins: 分箱质量（需 woe_data_csv=comp_woe_iv产出的woe_data.csv）\n"
            "- check_leakage: 数据穿越检验（需 train_csv+test_csv+mth_col 检时间切分；"
            "可选 iv_data_csv=comp_woe_iv产出的iv_data.csv 检目标泄漏；"
            "推荐再加 woe_data_csv=comp_woe_iv产出的woe_data.csv（重算修正IV）与 "
            "csv_path+cols（原始数据层|corr|>0.9检测），三层通道可检出被adj_bin洗白的完美泄漏特征）\n"
            "- check_overfit: 过拟合检验（需 confusion_csv=report产出的confusion_matrix_*.xlsx；"
            "非标准格式文件会触发D0结构警告，不构成有效输入）\n"
            "- check_stability: PSI稳定性分级（需 psi_data_csv=get_psi产出，xlsx亦可；"
            "缺psi列的文件会触发E0结构警告）\n"
            "- check_all: 根据提供的文件执行全部可用检查\n\n"
            "【建议调用时机】comp_woe_iv 之后 → check_bins/check_data；"
            "切分数据后 → check_leakage；report 之后 → check_overfit。\n"
            "执行后结果注入 python 会话: harness_report(发现列表)/harness_findings"
        )

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["check_data", "check_bins", "check_leakage", "check_overfit", "check_stability", "check_all"]},
                "csv_path": {"type": "string", "description": "数据文件路径（check_data/check_all）"},
                "cols": {"type": "string", "description": "JSON格式列名；check_data 不传时取除 target 外全部列"},
                "target": {"type": "string", "default": "target"},
                "nan": {"type": "number", "description": "缺失填充值（check_data 检查其合理性）"},
                "woe_data_csv": {"type": "string", "description": "comp_woe_iv 产出的 woe_data.csv（check_bins 必需；check_leakage 传它可从分箱明细重算修正IV，检出被adj_bin洗白的完美泄漏特征）"},
                "iv_data_csv": {"type": "string", "description": "comp_woe_iv 产出的 iv_data.csv（check_leakage 检目标泄漏）"},
                "train_csv": {"type": "string", "description": "训练集路径（check_leakage 检时间切分）"},
                "test_csv": {"type": "string", "description": "测试集路径（check_leakage 检时间切分）"},
                "mth_col": {"type": "string", "description": "月份列名（check_leakage 检时间切分）"},
                "confusion_csv": {"type": "string", "description": "report 产出的 confusion_matrix_*.xlsx（check_overfit，csv亦可）"},
                "psi_data_csv": {"type": "string", "description": "get_psi 产出的 PSI 数据（check_stability，xlsx亦可）"}
            },
            "required": ["method"]
        }

    def execute(self, **kwargs):
        self._findings_buffer = []  # 清空上次残留（异常路径可能未取走）
        try:
            method = kwargs.get("method")
            findings = []
            inputs_seen = []  # 修复(v5.3.1)：记录实际被识别的输入，区分"未提供数据"与"数据干净"

            def _missing_input(param_name: str, path) -> str:
                """文件不存在时的可诊断错误文案（修复原报"需要参数"的误导）"""
                if not path:
                    return f"{param_name} 参数未提供"
                return f"{param_name} 指向的文件不存在: {path}"

            # ═══ check_data: A 组缺失填充 ═══
            if method in ("check_data", "check_all"):
                csv_path = kwargs.get("csv_path")
                if csv_path and os.path.exists(csv_path):
                    inputs_seen.append("csv_path")
                    df = pd.read_csv(csv_path)
                    cols = kwargs.get("cols")
                    cols = json.loads(cols) if isinstance(cols, str) else cols
                    if cols is None:
                        target = kwargs.get("target", "target")
                        cols = [c for c in df.columns if c != target]
                    findings += check_missing_fill(df, cols, nan=kwargs.get("nan"))
                    # 修复(v5.3.1) 问题1: 数据无缺失但传了 nan 参数 → A0 假阴性风险提示
                    # （预填充数据中 A1/A2 依赖的 NaN 已丢失，危险填充值检不出来）
                    findings += check_prefilled_suspicion(df, cols, kwargs.get("nan"))
                    self._expose(harness_df=df)
                elif method == "check_data":
                    return ToolResult(success=False, output="",
                                      error=_missing_input("csv_path", csv_path))

            # ═══ check_bins: B 组分箱质量 ═══
            if method in ("check_bins", "check_all"):
                woe_csv = kwargs.get("woe_data_csv")
                if woe_csv and os.path.exists(woe_csv):
                    inputs_seen.append("woe_data_csv")
                    woe_data = pd.read_csv(woe_csv)
                    findings += check_bins_quality(woe_data)
                    self._expose(harness_woe=woe_data)
                elif method == "check_bins":
                    return ToolResult(success=False, output="",
                                      error=_missing_input(
                                          "woe_data_csv（comp_woe_iv 产出的 woe_data.csv）", woe_csv))

            # ═══ check_leakage: C 组数据穿越 ═══
            if method in ("check_leakage", "check_all"):
                train_csv = kwargs.get("train_csv")
                test_csv = kwargs.get("test_csv")
                mth_col = kwargs.get("mth_col")
                # 修复(v5.3.1)：缺 mth_col / 文件不存在时不得静默"全部通过"，
                # 显式提示该项检查未执行（假成功比报错更危险）
                if method == "check_leakage" and (train_csv or test_csv):
                    problems = []
                    if not (train_csv and os.path.exists(train_csv)):
                        problems.append(_missing_input("train_csv", train_csv))
                    if not (test_csv and os.path.exists(test_csv)):
                        problems.append(_missing_input("test_csv", test_csv))
                    elif not mth_col:
                        problems.append(
                            "mth_col 参数未提供 —— 时间穿越(C1)检查无法执行。"
                            "若数据无月份列请改用 iv_data_csv 检目标泄漏(C5)")
                    if problems:
                        return ToolResult(success=False, output="",
                                          error="时间切分检查输入不完整：\n- " + "\n- ".join(problems))
                if train_csv and test_csv and mth_col \
                        and os.path.exists(train_csv) and os.path.exists(test_csv):
                    inputs_seen.append("time_split")
                    train, test = pd.read_csv(train_csv), pd.read_csv(test_csv)
                    findings += check_time_split(train, test, mth_col)
                elif method == "check_all" and train_csv and test_csv and not mth_col:
                    findings.append(Finding(
                        "INFO", "C1",
                        "已提供 train_csv/test_csv 但未提供 mth_col，时间穿越(C1)检查未执行",
                        "评分卡/时间序列场景请补充 mth_col 参数后重新检查"))
                iv_csv = kwargs.get("iv_data_csv")
                woe_csv = kwargs.get("woe_data_csv")
                # 修复(v5.3.1) C5++: csv_path（原始数据层相关检测）也是合法输入通道之一
                _c5csv = kwargs.get("csv_path")
                _c5_valid = _c5csv and os.path.exists(_c5csv)
                if iv_csv and os.path.exists(iv_csv):
                    inputs_seen.append("iv_data_csv")
                    iv_data = pd.read_csv(iv_csv)
                    findings += check_bin_inf_vars(iv_data)
                    findings += check_target_leakage(iv_data)
                elif method == "check_leakage" and not (train_csv and test_csv) \
                        and not woe_csv and not _c5_valid:
                    # 修复(v5.3.1): 区分"参数未提供"与"文件不存在"，防误导
                    return ToolResult(
                        success=False, output="",
                        error="check_leakage 输入不完整：\n- "
                              + (_missing_input("iv_data_csv", iv_csv) if iv_csv else
                                 "iv_data_csv 参数未提供；也未提供 train_csv+test_csv+mth_col / "
                                 "woe_data_csv / csv_path+cols 任一通道。"
                                 "完整用法：train_csv+test_csv+mth_col 检时间切分；"
                                 "iv_data_csv+woe_data_csv 检目标泄漏（修正IV）；"
                                 "csv_path+cols 做原始数据层泄漏检测"))
                # 修复(v5.3.1) 问题3: 从 woe_data 重算修正 IV 检目标泄漏（C5 补强）—
                # 完美泄漏特征经 adj_bin 后 IV 被置 0 洗白，仅看 iv_data 必漏报，
                # 从分箱明细 good/bad_cnt 连续化重算修正 IV 兜底
                if woe_csv and os.path.exists(woe_csv):
                    inputs_seen.append("woe_data_csv")
                    _woe = pd.read_csv(woe_csv)
                    c5_findings, c5_corrected = check_leakage_from_woe(_woe)
                    findings += c5_findings
                    self._expose(harness_leakage_corrected_iv=c5_corrected)
                # 修复(v5.3.1) C5 终极兜底: 原始数据级点二列相关检测——
                # 极端参数下泄漏特征可能被 adj_bin 合并为单箱（分箱明细失效），
                # 用 csv_path 数据直接算 |corr|>0.9，不受任何分箱/置0影响
                if _c5_valid:
                    inputs_seen.append("csv_path")
                    _c5df = pd.read_csv(_c5csv)
                    _c5cols = kwargs.get("cols")
                    _c5cols = json.loads(_c5cols) if isinstance(_c5cols, str) else _c5cols
                    if not _c5cols:
                        _t = kwargs.get("target", "target")
                        _c5cols = [c for c in _c5df.columns if c not in (_t, kwargs.get("mth_col"))]
                    findings += check_target_leakage_raw(_c5df, _c5cols,
                                                         kwargs.get("target", "target"))

            # ═══ check_overfit: D 组过拟合 ═══
            if method in ("check_overfit", "check_all"):
                confusion_path = kwargs.get("confusion_csv")
                if confusion_path and os.path.exists(confusion_path):
                    inputs_seen.append("confusion_csv")
                    cm = _read_table(confusion_path)
                    # 修复(v5.3.1) 问题2: 结构校验（D0）——手工模拟/其他来源文件缺
                    # type[auc/ks] 列时 D1/D4 会静默跳过，先校验结构防假阴性
                    findings += check_confusion_schema(cm)
                    findings += check_overfit(cm)
                elif method == "check_overfit":
                    return ToolResult(
                        success=False, output="",
                        error=_missing_input(
                            "confusion_csv（report 产出的 confusion_matrix_*.xlsx）", confusion_path))

            # ═══ check_stability: E 组 PSI ═══
            if method in ("check_stability", "check_all"):
                psi_path = kwargs.get("psi_data_csv")
                if psi_path and os.path.exists(psi_path):
                    inputs_seen.append("psi_data_csv")
                    psi = _read_table(psi_path)
                    # 修复(v5.3.1) 问题2: 结构校验（E0）——缺 psi 列时 E1 静默跳过，
                    # 能定位候选列（psi_avg/psi_value 等）则提示改名，防假阴性
                    findings += check_psi_schema(psi)
                    findings += check_psi(psi)
                elif method == "check_stability":
                    return ToolResult(
                        success=False, output="",
                        error=_missing_input(
                            "psi_data_csv（get_psi 产出的 PSI 数据）", psi_path))

            # 修复(v5.3.1)：按"是否有可用输入"判定，而非"findings 是否为空"——
            # 数据干净时不再伪装成参数错误，正确输出"全部通过"
            if not inputs_seen and method == "check_all":
                return ToolResult(
                    success=False, output="",
                    error="check_all 未识别到任何可用输入：请至少提供 csv_path/woe_data_csv/"
                          "train_csv+test_csv+mth_col/iv_data_csv/confusion_csv/psi_data_csv 之一")

            # 审计 + 报告 + 注入会话
            self._log_findings(findings)
            report = HarnessReport(findings)
            self._expose(
                harness_report=[f.__dict__ for f in findings],
                harness_findings=findings,
            )

            output = (
                f"🧪 cbhpacks_harness.{method} 检查完成\n\n"
                f"{report.format('建模护栏检查')}"
            )
            return ToolResult(success=True, output=output,
                              harness_findings=self._pop_findings())

        except Exception as e:
            import traceback
            return ToolResult(success=False, output="", error=f"执行失败: {str(e)}\n{traceback.format_exc()}")
