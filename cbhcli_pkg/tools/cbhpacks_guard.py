"""cbhpacks 领域 Harness 检查模块 — 数据科学建模确定性护栏（v5.3.1）

Harness Engineering 理念（Mitchell Hashimoto 提出）：让模型错误在结构上更难发生。
本模块是 cbhpacks 工具的"确定性策略引擎"层——所有检查由代码计算，不依赖 LLM 自觉。

检查项分级：
  BLOCK — 确定性错误（数学必然），供工具层拦截
  WARN  — 高风险，警告 + 修复建议（追加到工具输出）
  INFO  — 提示性建议

检查项清单：
  A 组 缺失填充: A0 预填充假阴性提示 / A1 填充值≥非缺失最小值 / A2 填充值与已有值冲突 / A3 高缺失率
  B 组 分箱质量: B1 单好/单坏箱(WOE=±inf) / B2 单箱占比>50%粒度太粗 / B3 小样本箱 / B4 WOE非单调
  C 组 数据穿越: C1 训练/测试时间重叠 / C2 跨数据集WOE泄漏 / C5 目标泄漏(iv_data IV过高
                 + woe_data修正IV重算 + 原始数据|corr|>0.9 三层检测)
  D 组 过拟合:   D0 混淆矩阵结构校验 / D1 train/test指标差 / D2 特征-样本比(LR) / D3 CV折数 / D4 test显著优于train
  E 组 稳定性:   E0 PSI表结构校验 / E1 PSI分级解读

使用方：
  - cbhpacks_bins / cbhpacks_training 等工具内置调用（B/C/D 组）
  - cbhpacks_harness 工具显式调用（全部检查项）

设计原则：纯函数、绝不抛异常（检查失败返回空清单，绝不影响主流程）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import numpy as np

# 分级常量
BLOCK = "BLOCK"
WARN = "WARN"
INFO = "INFO"


@dataclass
class Finding:
    """单项检查发现"""
    level: str                 # BLOCK / WARN / INFO
    code: str                  # 检查项代码: A1/B1/C1/D1/E1...
    message: str               # 发现描述
    fix: str = ""              # 修复建议

    def format(self) -> str:
        icon = {"BLOCK": "🔴", "WARN": "🟡", "INFO": "🔵"}.get(self.level, "•")
        line = f"{icon} [{self.level} {self.code}] {self.message}"
        if self.fix:
            line += f"\n    → 建议: {self.fix}"
        return line


@dataclass
class HarnessReport:
    """一次检查的汇总报告"""
    findings: list = field(default_factory=list)

    @property
    def blocks(self) -> list:
        return [f for f in self.findings if f.level == BLOCK]

    @property
    def warns(self) -> list:
        return [f for f in self.findings if f.level == WARN]

    @property
    def infos(self) -> list:
        return [f for f in self.findings if f.level == INFO]

    def format(self, title: str = "Harness 检查") -> str:
        if not self.findings:
            return f"✅ {title}: 全部通过，未发现问题"
        lines = [f"⚠️ {title}: {len(self.warns)} 项警告, {len(self.infos)} 项提示"]
        for f in self.findings:
            lines.append(f.format())
        return "\n".join(lines)


def format_findings(findings: list) -> str:
    """格式化为报告文本（供工具输出追加）"""
    return HarnessReport(findings).format()


def _safe(func):
    """装饰器：检查函数任何异常都返回空清单（护栏绝不破坏主流程）"""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            return []
    return wrapper


# ══════════════════════════════════════════════════════════════════
# A 组 — 缺失填充合理性
# ══════════════════════════════════════════════════════════════════

@_safe
def check_missing_fill(df, cols, nan: Optional[float] = None) -> list:
    """A0/A1/A2/A3: 缺失值填充合理性检查。

    Args:
        df: 数据集 DataFrame
        cols: 参与检查的列名列表
        nan: 缺失填充值（None 表示未启用填充）

    修复(v5.3.1) 假阴性问题1：本检查依赖数据中的 NaN 检测填充值安全性，
    若调用方已 fillna 再传入则无缺失信息、全部静默通过（假阴性）。
    该场景由 harness 层 A0 提示（check_data 无法从数据本身识别"已预填充"，
    但 harness 知道本次是否传了 nan 参数，见 cbhpacks_harness.check_data）。
    """
    findings = []
    if df is None or not cols:
        return findings
    for col in cols:
        if col not in df.columns:
            continue
        s = df[col]
        non_null = s.dropna()
        if len(non_null) == 0:
            continue
        # A3 高缺失率（仅 INFO，缺失本身可能有业务信息）
        miss_rate = s.isna().mean()
        if miss_rate > 0.5:
            findings.append(Finding(
                INFO, "A3",
                f"变量 {col} 缺失率 {miss_rate:.1%} > 50%",
                "考虑将缺失单独分箱（缺失本身有信息量）或删除该变量"))
        if nan is None or s.isna().sum() == 0:
            continue  # 无缺失值则填充不生效，无需检查填充值
        if not pd.api.types.is_numeric_dtype(non_null):
            continue
        # A1 填充值落入正常值区间
        vmin = float(non_null.min())
        if float(nan) >= vmin:
            findings.append(Finding(
                WARN, "A1",
                f"变量 {col}: 缺失填充值 {nan} ≥ 非缺失最小值 {vmin}",
                f"填充值会混入正常值区间参与分箱。建议用远小于最小值的值(如-999)；"
                f"若 {nan} 有业务语义（如'从未发生'=0）可忽略本警告"))
        # A2 填充值与已有值冲突
        if float(nan) in set(non_null.unique()):
            findings.append(Finding(
                WARN, "A2",
                f"变量 {col}: 缺失填充值 {nan} 与已有取值冲突",
                "填充值恰好等于真实取值，分箱时无法区分缺失与真实值"))
    return findings


# A0: 底层无法从数据识别"已预填充"，由 harness 层在有 nan 参数但 data 无缺失时触发
def check_prefilled_suspicion(df, cols, nan) -> list:
    """A0: 数据无缺失但传了 nan 参数 → 提示假阴性风险（修复问题1）。

    check_data 的 A1/A2 检查依赖数据中的 NaN：若数据已被预填充（如
    df.fillna(0).to_csv() 后再传入），数据里无缺失信息，危险填充值
    （如 0 ≥ 正常值区间）将完全检不出来——静默"全部通过"。
    """
    findings = []
    if nan is None or df is None or not cols:
        return findings
    has_nan = any(c in df.columns and df[c].isna().any() for c in cols)
    if has_nan:
        return findings
    findings.append(Finding(
        INFO, "A0",
        "数据中未检测到任何缺失值，但本次传入了 nan 填充参数——"
        "A1/A2 填充值合理性检查跳过（预填充数据无法评估填充值安全性）",
        "若该数据集此前已用 df.fillna() 预填充，请改传原始含 NaN 数据重新 check_data；"
        "若数据本身确实无缺失，可忽略本提示"))
    return findings


# ══════════════════════════════════════════════════════════════════
# B 组 — 分箱质量（结果驱动，基于 woe_data 实际分布）
# ══════════════════════════════════════════════════════════════════

@_safe
def check_bins_quality(woe_data, n_samples: Optional[int] = None) -> list:
    """B1/B2/B3/B4: 分箱质量检查（基于分箱结果，非 group 参数）。

    判断依据是分箱产出的实际样本分布，样本量维度天然被 total_cnt 捕获：
    千万级数据 15 箱每箱几十万样本 → 检查自然通过。

    Args:
        woe_data: comp_woe_iv 产出的 woe_data（列: col_name/bucket/good_cnt/bad_cnt/total_cnt/woe/iv_bin...）
        n_samples: 样本总量（None 时按第一个变量的 total_cnt 求和近似）
    """
    findings = []
    if woe_data is None or not isinstance(woe_data, pd.DataFrame) or woe_data.empty:
        return findings
    need = ("col_name", "total_cnt")
    if not all(c in woe_data.columns for c in need):
        return findings

    bad_col = "bad_cnt" if "bad_cnt" in woe_data.columns else None
    good_col = "good_cnt" if "good_cnt" in woe_data.columns else None
    woe_col = "woe" if "woe" in woe_data.columns else None

    # 样本量近似（每变量 total_cnt 之和相等，取第一个变量）
    if n_samples is None:
        first = woe_data[woe_data["col_name"] == woe_data["col_name"].iloc[0]]
        n_samples = int(first["total_cnt"].sum()) if len(first) else 0

    min_bin_cnt = max(30, int(n_samples * 0.005)) if n_samples else 30

    for col_name, grp in woe_data.groupby("col_name"):
        # B1 单好/单坏箱 → WOE 数学上必为 ±inf
        zero_parts = []
        if bad_col and (grp[bad_col] == 0).any():
            zero_parts.append("坏样本")
        if good_col and (grp[good_col] == 0).any():
            zero_parts.append("好样本")
        if zero_parts:
            # 修复(v5.3.1)：按 bad=0 **或** good=0 统计（原实现只数 bad=0，
            # good=0 的箱被漏数，极端时出现"0 个无穷大箱"的荒谬文案）
            n_zero = int(((grp[bad_col] == 0) | (grp[good_col] == 0)).sum()) \
                if (bad_col and good_col) else \
                int(((grp[bad_col] == 0) if bad_col else (grp[good_col] == 0)).sum())
            # 修复(v5.3.1) 文案瑕疵：bad=0 与 good=0 分开计数，
            # "无坏样本/好样本"读起来像"两者皆无"，实为"或"关系
            detail = []
            if bad_col and (grp[bad_col] == 0).any():
                detail.append(f"{int((grp[bad_col] == 0).sum())} 个坏样本=0")
            if good_col and (grp[good_col] == 0).any():
                detail.append(f"{int((grp[good_col] == 0).sum())} 个好样本=0")
            findings.append(Finding(
                WARN, "B1",
                f"变量 {col_name}: 共 {n_zero} 个单侧箱（{ '，'.join(detail) }），"
                f"这些箱的 WOE 为无穷大（已被库置 0）",
                "建议 adj_bin=True 合并相邻箱，或调大 min_group 重新分箱；"
                "若该区间好/坏样本全为 0 是业务真实情况可接受"))
        # B2 粒度下限：单箱样本占比 > 50%
        if n_samples:
            max_share = float(grp["total_cnt"].max() / n_samples)
            if max_share > 0.5:
                findings.append(Finding(
                    WARN, "B2",
                    f"变量 {col_name}: 单箱样本占比 {max_share:.1%} > 50%，分箱粒度太粗",
                    "区分度不足，建议增加分箱数或更换分箱方式"))
        # B3 粒度上限：小样本箱
        small = grp[grp["total_cnt"] < min_bin_cnt]
        if len(small):
            findings.append(Finding(
                WARN, "B3",
                f"变量 {col_name}: 存在 {len(small)} 个样本数 < {min_bin_cnt} 的小样本箱",
                "箱内样本过少 WOE 噪音大，建议减少分箱数或 adj_bin=True 合并"))
        # B4 WOE 单调性（仅数值变量分箱有序时有意义，箱数>=3）
        # 修复(v5.3.1)：cat_bin 类别分箱无箱序，单调性无意义 → 按 bins_type 列跳过
        if woe_col and len(grp) >= 3:
            _bins_type = str(grp["bins_type"].iloc[0]) if "bins_type" in grp.columns else ""
            if _bins_type == "cat_bin":
                continue  # 类别分箱无序，跳过单调性检查（防误报）
            woe_vals = grp[woe_col].replace([np.inf, -np.inf], np.nan).dropna()
            if len(woe_vals) >= 3:
                diffs = np.sign(np.diff(woe_vals.values))
                if not (np.all(diffs >= 0) or np.all(diffs <= 0)):
                    findings.append(Finding(
                        INFO, "B4",
                        f"变量 {col_name}: WOE 随箱序非单调",
                        "可能原因：分箱方式/箱数不合适、极端值或噪音干扰、业务上本身为非线性关系。"
                        "建议依次尝试：① 调整分箱参数（增减箱数、adj_bin=True 合并相邻箱）；"
                        "② 更换其他分箱方式（等距/卡方/决策树等）对比；"
                        "③ 若业务上确为非线性关系（如 U 型），可保留并人工确认"))
    return findings


@_safe
def check_bin_inf_vars(iv_data) -> list:
    """B1 补充: iv_data 中 IV 为 inf 的变量（原静默置 0 前先提取警告清单）"""
    findings = []
    if iv_data is None or not isinstance(iv_data, pd.DataFrame):
        return findings
    if "iv_value" not in iv_data.columns or "var" not in iv_data.columns:
        return findings
    inf_vars = iv_data.loc[np.isinf(iv_data["iv_value"]), "var"].tolist()
    if inf_vars:
        findings.append(Finding(
            WARN, "B1",
            f"以下变量的 IV 为无穷大（已置 0，但分箱存在问题）: {', '.join(map(str, inf_vars))}",
            "根因是存在单好/单坏箱。建议 adj_bin=True 或调大 min_group 重新分箱，"
            "否则这些变量的 IV 被低估"))
    return findings


# ══════════════════════════════════════════════════════════════════
# C 组 — 数据穿越检验
# ══════════════════════════════════════════════════════════════════

@_safe
def check_time_split(train, test, mth_col: Optional[str] = None) -> list:
    """C1: 训练/测试时间切分检验（场景化警告，不硬拦截）。

    评分卡/时间序列预测场景 train 月份必须 < test 月份；
    无时间语义的横截面分析随机打乱是正确操作，由用户判断场景。
    """
    findings = []
    if not mth_col or train is None or test is None:
        return findings
    if mth_col not in train.columns or mth_col not in test.columns:
        return findings
    train_max = train[mth_col].max()
    test_min = test[mth_col].min()
    if test_min <= train_max:
        findings.append(Finding(
            WARN, "C1",
            f"训练/测试时间重叠: train 最大 {mth_col}={train_max}, test 最小 {mth_col}={test_min}",
            "若目标是评分卡/时间序列预测（OOT 验证），train 月份必须 < test 月份，"
            "随机切分会发生数据穿越；若为无时间语义的横截面分析，可忽略本警告"))
    return findings


@_safe
def check_target_leakage(iv_data, iv_thres: float = 0.5) -> list:
    """C5: 目标泄漏检测（iv_data 通道）— 单变量 IV 异常高通常意味着该特征是 target 衍生变量。

    局限：只看库报告的 iv_value。完美泄漏特征（如 target*10）经 adj_bin 分箱后
    IV 被置 0"洗白"时本通道漏报 —— 由 check_leakage_from_woe（修正IV重算）与
    check_target_leakage_raw（原始数据相关）两条补强通道兜底。
    """
    findings = []
    if iv_data is None or not isinstance(iv_data, pd.DataFrame):
        return findings
    if "iv_value" not in iv_data.columns or "var" not in iv_data.columns:
        return findings
    high = iv_data[iv_data["iv_value"].replace([np.inf, -np.inf], np.nan) > iv_thres]
    for _, row in high.iterrows():
        findings.append(Finding(
            WARN, "C5",
            f"变量 {row['var']}: IV={row['iv_value']:.3f} > {iv_thres}，疑似目标衍生变量",
            "该特征可能是 target 的衍生/代理变量（数据泄漏），请人工确认后决定是否入模"))
    return findings


def check_leakage_from_woe(woe_data, iv_thres: float = 0.5):
    """C5+ 从 woe_data 重算修正 IV 的目标泄漏检测（修复问题3）。

    背景：cbhpacks 库 bins_rpt 遇到单好/单坏箱（如完美泄漏特征分出的
    坏率 0%/100% 箱）会把 WOE/IV 置 0（防 NaN 传播），adj_bin 合并后
    报告 IV 常为 0 或极低 → 只看 iv_data 的 C5 检查被"洗白"漏报。
    本检查从 woe_data 的 good_cnt/bad_cnt 独立重算：单侧箱（某一侧=0）
        用 ±0.5 连续化修正（对数几率 ±ln(0.999/0.001)≈±6.9，与统计上
        "极端但非完美"的特征量级一致），完美泄漏修正后 IV>5 必然报警。

    注意：故意不加 @_safe 装饰器——本函数返回元组 (findings, corrected)，
    @_safe 异常时返回 [] 会导致调用方解包 ValueError（护栏反而破坏主流程），
    改为内部 try/except 恒返回二元组（护栏绝不破坏主流程）。

    Args:
        woe_data: comp_woe_iv 产出的分箱明细（列: col_name/bad_cnt/good_cnt/total_cnt...）
    Returns:
        (findings, corrected_iv_map): 发现列表 + {var: 修正IV} 字典
    """
    findings = []
    corrected = {}
    try:
        if woe_data is None or not isinstance(woe_data, pd.DataFrame) or woe_data.empty:
            return findings, corrected
        need = ("col_name", "good_cnt", "bad_cnt", "total_cnt")
        if not all(c in woe_data.columns for c in need):
            return findings, corrected
        for col_name, grp in woe_data.groupby("col_name"):
            good = grp["good_cnt"].astype(float).values
            bad = grp["bad_cnt"].astype(float).values
            tot = good + bad
            if tot.sum() == 0:
                continue
            # 连续化修正：单侧箱（good=0 或 bad=0）计数 ±0.5，防 0 崩溃
            good_adj = np.where(good == 0, 0.5, good)
            bad_adj = np.where(bad == 0, 0.5, bad)
            bad_total, good_total = bad_adj.sum(), good_adj.sum()
            badattr = bad_adj / bad_total
            goodattr = good_adj / good_total
            woe = np.log(badattr / goodattr)
            iv_corrected = float(((badattr - goodattr) * woe).sum())
            corrected[col_name] = iv_corrected
            if iv_corrected > iv_thres:
                # 找到库报告的 IV（可能被置 0）
                rpt_iv = None
                for ivc in ("iv", "iv_value"):
                    if ivc in grp.columns and len(grp[ivc].dropna()):
                        rpt_iv = float(grp[ivc].dropna().iloc[0])
                        break
                seen = f"库报告 IV={rpt_iv:.3f}（被 WOE 置 0 洗白）" if rpt_iv is not None and rpt_iv < 0.1 \
                    else f"库报告 IV={rpt_iv:.3f}" if rpt_iv is not None else "IV 未知"
                findings.append(Finding(
                    WARN, "C5",
                    f"变量 {col_name}: 修正 IV={iv_corrected:.3f} > {iv_thres}（{seen}），"
                    f"存在单好/单坏箱，疑似 target 直接派生（完美泄漏）",
                    "该特征极可能是 target 的衍生/代理变量（如 target×常数），"
                    "经 adj_bin 后 IV 被置 0 掩盖。请人工确认后剔除，禁止直接入模"))
    except Exception:
        # 护栏绝不破坏主流程：任何异常返回空结果（不抛出）
        return [], {}
    return findings, corrected


@_safe
def check_target_leakage_raw(df, cols, target: str, corr_thres: float = 0.9) -> list:
    """C5++ 从原始数据计算特征-目标点二列相关，检测完美泄漏（终极兜底）。

    修复(v5.3.1) 问题3的边界场景：极端参数下完美泄漏特征经 adj_bin 可能被
    合并为单箱（如 group 过小），分箱明细也失去信息 → C5/C5+ 均检不出。
    本检查直接在原始数据层算 Pearson 相关（对 0/1 target 即点二列相关）：
    完美泄漏特征 |corr| 接近 1（如 target*10+噪声 ≈ 0.99+），正常特征 <0.3，
    阈值 0.9 两端余量都很大，且完全不受分箱/WOE 置 0 影响。
    """
    findings = []
    if df is None or not isinstance(df, pd.DataFrame) or not cols or not target:
        return findings
    if target not in df.columns:
        return findings
    y = pd.to_numeric(df[target], errors="coerce")
    if y.nunique(dropna=True) != 2:
        return findings  # 非二分类跳过
    for col in cols:
        if col == target or col not in df.columns:
            continue
        x = pd.to_numeric(df[col], errors="coerce")
        if x.nunique(dropna=True) <= 1:
            continue
        corr = x.corr(y)
        if pd.isna(corr):
            continue
        if abs(corr) > corr_thres:
            findings.append(Finding(
                WARN, "C5",
                f"变量 {col}: 与 target 的相关系数 {corr:.4f}（|r|>{corr_thres}），"
                f"几乎确定性关联，疑似 target 直接派生（完美泄漏）",
                "该特征可能是 target 的衍生/代理变量（如 target×常数+微量噪声），"
                "即使 adj_bin 分箱也无法掩盖。请人工确认后剔除，禁止入模"))
    return findings


# ══════════════════════════════════════════════════════════════════
# D 组 — 过拟合检验
# ══════════════════════════════════════════════════════════════════

@_safe
def check_confusion_schema(cm) -> list:
    """D0: 混淆矩阵结构校验（修复问题2 — 手工模拟文件假阴性）。

    report 产出的标准表必有 type[train/test/all] + auc/ks 列。
    手工模拟/其他来源文件若缺这些列，D1/D4 检查会静默跳过、输出
    "全部通过"（假阴性）。结构不符时：能定位候选指标列 → WARN 提示
    改名映射；完全不像混淆矩阵 → WARN 提示检查未执行。
    """
    findings = []
    if cm is None or not isinstance(cm, pd.DataFrame) or cm.empty:
        return findings
    if "type" in cm.columns:
        types = set(cm["type"].astype(str).str.lower())
        if not {"train", "test"} <= types:
            findings.append(Finding(
                WARN, "D0",
                f"混淆矩阵 type 列缺少 train/test 行（当前: {sorted(types)}），"
                f"train/test 指标差检查（D1/D4）未执行",
                "本文件可能不是 report 产出的标准混淆矩阵。请使用 "
                "cbhpacks_binary_model report 产出的 confusion_matrix_*.xlsx"))
        return findings  # type 列已存在，列名语义一致，无需 further 校验
    # 无 type 列：尝试定位候选指标列，提示改名（常见: dataset/数据集, auc_val/auc_train...）
    metric_cands = {"auc": ["auc", "roc_auc", "AUC", "auc_val"],
                    "ks": ["ks", "KS", "ks_stat", "ks_val"]}
    mapped = {}
    for std, cands in metric_cands.items():
        for c in cm.columns:
            if str(c).strip().lower() in {x.lower() for x in cands}:
                mapped[std] = c
                break
    if mapped:
        findings.append(Finding(
            WARN, "D0",
            f"混淆矩阵缺少标准 type 列，无法做 train/test 指标差检查；"
            f"探测到疑似指标列 {mapped}，但行分组列缺失",
            "check_overfit 需 report 产出的 confusion_matrix_*.xlsx"
            "（含 type[train/test/all] + auc/ks 列）。手工模拟文件不构成本检查的有效输入"))
    else:
        findings.append(Finding(
            WARN, "D0",
            f"文件列名 {list(cm.columns)[:8]} 与 report 产出的混淆矩阵"
            f"（type[train/test/all] + auc/ks）不符，过拟合检查未执行",
            "请使用 cbhpacks_binary_model report 产出的 confusion_matrix_*.xlsx，"
            "或按标准 schema 手工构造后再检查"))
    return findings


@_safe
def check_overfit(confusion_df) -> list:
    """D1/D4: train/test 指标差检验。

    Args:
        confusion_df: report 产出的混淆矩阵（列: type[train/test/all], auc, ks, ...）
    """
    findings = []
    if confusion_df is None or not isinstance(confusion_df, pd.DataFrame):
        return findings
    if "type" not in confusion_df.columns:
        return findings
    try:
        row_t = confusion_df[confusion_df["type"] == "train"]
        row_v = confusion_df[confusion_df["type"] == "test"]
        if not len(row_t) or not len(row_v):
            return findings
        if "auc" in confusion_df.columns:
            auc_gap = float(row_t["auc"].iloc[0]) - float(row_v["auc"].iloc[0])
            if auc_gap > 0.05:
                findings.append(Finding(
                    WARN, "D1",
                    f"过拟合风险: AUC(train) - AUC(test) = {auc_gap:.4f} > 0.05",
                    "建议增强正则化 / 减少特征数 / 增加训练样本"))
            elif auc_gap < -0.03:
                findings.append(Finding(
                    WARN, "D4",
                    f"异常: AUC(test) 比 AUC(train) 高 {-auc_gap:.4f}",
                    "test 显著优于 train 通常意味着切分异常或数据泄漏，请检查切分方式"))
        if "ks" in confusion_df.columns:
            ks_gap = float(row_t["ks"].iloc[0]) - float(row_v["ks"].iloc[0])
            if ks_gap > 0.1:
                findings.append(Finding(
                    WARN, "D1",
                    f"过拟合风险: KS(train) - KS(test) = {ks_gap:.4f} > 0.1",
                    "建议增强正则化 / 减少特征数 / 增加训练样本"))
    except Exception:
        pass
    return findings


@_safe
def check_feature_ratio(model_type: str, n_features: int, n_pos_samples: int) -> list:
    """D2: 特征-样本比（经验法则：LR 每特征至少 10 个正样本）。"""
    findings = []
    if model_type not in ("lr",):
        return findings
    if n_features and n_pos_samples and n_features > n_pos_samples / 10:
        findings.append(Finding(
            WARN, "D2",
            f"LR 模型特征数 {n_features} > 正样本数/10 ({n_pos_samples / 10:.0f})",
            "特征相对样本过多易过拟合，建议先做特征筛选或改用正则化更强的模型"))
    return findings


@_safe
def check_cv_folds(cv: int) -> list:
    """D3: 调参交叉验证折数提示。"""
    findings = []
    if cv and cv < 5:
        findings.append(Finding(
            INFO, "D3",
            f"调参交叉验证折数 cv={cv} 偏小",
            "折数过小最优参数方差大、结果不可靠，建议 cv≥5"))
    return findings


# ══════════════════════════════════════════════════════════════════
# E 组 — 稳定性
# ══════════════════════════════════════════════════════════════════

@_safe
def check_psi_schema(psi_data) -> list:
    """E0: PSI 表结构校验（修复问题2 — 手工模拟文件假阴性）。

    get_psi 标准产出含 var + psi 列。手工模拟文件缺 psi 列时 E1
    静默跳过、输出"全部通过"（假阴性）。能定位候选 PSI 列（psi_avg/
    psi_value 等）→ WARN 提示改名；完全不像 → WARN 提示检查未执行。
    """
    findings = []
    if psi_data is None or not isinstance(psi_data, pd.DataFrame) or psi_data.empty:
        return findings
    has_psi = any(c in psi_data.columns for c in ("psi", "psi_value", "PSI"))
    if has_psi:
        return findings
    # 无标准 psi 列：尝试定位候选列名（get_psi 变体/手工常用名）
    cand = None
    for c in psi_data.columns:
        if "psi" in str(c).strip().lower():
            cand = c
            break
    if cand:
        findings.append(Finding(
            WARN, "E0",
            f"PSI 表缺少标准 psi 列，检测到疑似列 '{cand}'，稳定性分级(E1)未执行",
            f"请将 {cand} 重命名为 psi（或转存标准表）后重新 check_stability；"
            f"get_psi 产出的 psi_single_rpt_*.xlsx / psi_avg_rpt_*.xlsx 为标准格式"))
    else:
        findings.append(Finding(
            WARN, "E0",
            f"文件列名 {list(psi_data.columns)[:8]} 中无 PSI 值列，稳定性检查未执行",
            "请使用 cbhpacks_bins_model.get_psi 产出的 psi_single_rpt_*.xlsx"
            "（含 var+psi 列），或按标准 schema 构造后再检查"))
    return findings


@_safe
def check_psi(psi_data) -> list:
    """E1: PSI 分级解读（业界通用阈值: <0.1 稳定, 0.1~0.25 漂移, >0.25 严重漂移）。

    Args:
        psi_data: get_psi 产出的数据（含 PSI 值列，自动探测 psi/psi_value 列）
    """
    findings = []
    if psi_data is None or not isinstance(psi_data, pd.DataFrame):
        return findings
    psi_col = None
    for cand in ("psi", "psi_value", "PSI"):
        if cand in psi_data.columns:
            psi_col = cand
            break
    if psi_col is None:
        return findings
    for _, row in psi_data.iterrows():
        v = row[psi_col]
        if pd.isna(v):
            continue
        v = float(v)
        var = row.get("var", row.get("col_name", "?"))
        if v > 0.25:
            findings.append(Finding(
                WARN, "E1",
                f"变量 {var}: PSI={v:.4f} > 0.25，特征严重漂移",
                "该变量分布发生重大变化，建议排查数据源或剔除该变量"))
        elif v > 0.1:
            findings.append(Finding(
                WARN, "E1",
                f"变量 {var}: PSI={v:.4f} ∈ (0.1, 0.25]，特征轻微漂移",
                "持续监控该变量，必要时重新分箱"))
    return findings
