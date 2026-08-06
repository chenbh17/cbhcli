# cbhpacks 使用方法（v1.0.0）

> 本文档依据 cbhpacks 源代码逐行核对生成，覆盖**全部模块、类、函数、方法、参数、输入/输出格式、输出文件**。
> 用途：作为 Agent 系统提示词，指导 AI 编写基于 cbhpacks 的数据科学/风控建模代码。
> ⚠️ 源代码中部分类的 docstring 示例已过时（与真实签名不符），本文档一律以**真实函数签名**为准，差异处已用「⚠️ 与源码 docstring 的差异」标出。

---

## 0. 总览与全局约定

### 0.1 导入

> ⚠️ **必须使用子模块全路径导入**（2026-08-05 实测修正）：本机安装的 cbhpacks 其 `__init__.py` 为**空文件**（不导出任何符号，无 `__all__`），`from cbhpacks import *` 或 `from cbhpacks import xxx` 一律 **ImportError**。所有符号必须按「模块.符号」全路径导入：

```python
# 测试数据
from cbhpacks.models_for_try import get_random_data
# 分箱/WOE/IV/PSI
from cbhpacks.bins_model import get_bins, bins_model, chi_square_of_df_cols
# 模型训练
from cbhpacks.model_training import binary_model, uns_model, linear_model
# 特征选择
from cbhpacks.cols_select import cols_select, cols_select_js
# 特征编码
from cbhpacks.cols_encode import cols_encode
# 预处理/描述统计
from cbhpacks.preprocess import cols_operate, desc_df, desc_col
# Linux 连接
from cbhpacks.con_linux import con_linux, data_trans_linux, jps, hadoop, start_hive
# 数据库连接
from cbhpacks.con_sql import chrun, chdf, con_mysql, con_hive, get_create_table, to_hive, rfms_sql
```

全部 8 个模块、24 个公开符号（符号→模块归属表）：

| 类别 | 模块 | 符号 |
|---|---|---|
| 测试数据 | `cbhpacks.models_for_try` | `get_random_data` |
| 分箱/WOE/IV/PSI | `cbhpacks.bins_model` | `get_bins`, `bins_model`, `chi_square_of_df_cols` |
| 模型训练 | `cbhpacks.model_training` | `binary_model`, `uns_model`, `linear_model` |
| 特征选择 | `cbhpacks.cols_select` | `cols_select`, `cols_select_js` |
| 特征编码 | `cbhpacks.cols_encode` | `cols_encode` |
| 预处理/描述统计 | `cbhpacks.preprocess` | `cols_operate`, `desc_df`, `desc_col` |
| Linux 连接 | `cbhpacks.con_linux` | `con_linux`, `data_trans_linux`, `jps`, `hadoop`, `start_hive` |
| 数据库连接 | `cbhpacks.con_sql` | `chrun`, `chdf`, `con_mysql`, `con_hive`, `get_create_table`, `to_hive`, `rfms_sql` |

### 0.2 全局约定（所有类通用）

1. **数据容器**：`df` 一律为 `pandas.DataFrame`；`cols` 一律为 `list[str]`（特征列名列表）；`target` 一律为 `str`（目标列名）。
2. **二分类目标**：`target` 列必须是 **0/1 二值**（1=坏样本/正样本，0=好样本/负样本）。
3. **输出目录**：几乎所有类都有 `path` 参数，取值为**相对当前工作目录 `os.getcwd()` 的相对路径**，实例化时自动创建（`os.makedirs(exist_ok=True)`）。所有结果文件（xlsx/csv/pkl/png）写入该目录。
4. **缺失值**：多数分箱/筛选函数通过 `nan` 参数指定缺失值填充值（如 `-9999`），内部先 `fillna(nan)` 再计算。
5. **月份字段**：涉及 PSI/逐月分析的函数要求 `mth_col`（月份列名，str），`base_mth`/`cmp_mth`（基准月/比较月，int 或 str，如 `202401`，**建议 int**）。
6. **joblib 持久化**：`.pkl` 文件用 `joblib.load()` 读取；模型 `.pkl` 加载后可直接 `.predict()`/`.predict_proba()`。
7. **matplotlib 图片**：部分方法内含 `plt.show()`（无图形界面环境下忽略即可，png 已先行 `savefig` 保存）。

### 0.3 标准风控建模流水线（推荐顺序）

```
step0  desc_df / desc_col            数据探查、单变量分析        → step0_desc_result/
step1  cols_encode                   特征编码（标准化/分箱/WOE等）→ step1_cols_encode/
step2  bins_model                    分箱、WOE/IV、PSI、画图      → step2_bins_result/
step4  cols_select                   十种特征筛选（链式）         → step4_cols_select/
step5  cols_select_js                递归特征选择（定特征数）     → step5_cols_js/
step6  binary_model                  LR/XGB/LGBM/MLP/SVM/RDF 训练+报告 → step6_binary_model/
       uns_model / linear_model      无监督 / 线性回归            → step6_uns_model/ 等
```

### 0.4 端到端完整示例

```python
from cbhpacks.models_for_try import get_random_data
from cbhpacks.bins_model import bins_model
from cbhpacks.cols_select import cols_select
from cbhpacks.model_training import binary_model
import pandas as pd

# 1. 生成测试数据（真实场景替换为 pd.read_csv 等）
df = get_random_data(0, 100, 10000, 6)          # 1万行，月份 202401~202406
cols = ['col1','col2','col3','col4','col5','col6','col7','col8','col9']

# 2. 分箱 + WOE/IV + PSI
bm = bins_model(df=df, cols=cols, group=10, target='target', nan=-9999,
                bins_type='eq_cnt', mth_col='mth', base_mth=202401, cmp_mth=202403,
                col='col1', adj_bin=True, cat_cols=False, path='step2_bins_result')
woe_data, iv_data = bm.comp_woe_iv()            # 分箱报告 + IV 表
woe_df, woe_mapping = bm.data_to_woe()          # 全量数据 WOE 转换
psi_data = bm.get_psi()                          # 单月 PSI
psi_avg_data = bm.psi_mth_avg()                  # PSI 月均值
bm.plot_cols_rpt(show=False)                     # 每变量 4 张图

# 3. 特征筛选（链式，cols_s 逐步收缩）
cs = cols_select(df=df, cols=cols, target='target',
                 psi_data=psi_avg_data, iv_data=iv_data,
                 null_pct=0.95, enu_cnt=1, enu_pct=0.95, psi_thres=0.1, iv_thres=0.01,
                 corr_method='spearman', corr_thres=0.8, chi2_p_value_thres=0.5,
                 lg_method='recursion', lg_C=0.1, ml_method='lgb',
                 boot_method='lgb', boot_thres=100, vif_thres=10, nan=0,
                 path='step4_cols_select')
cs.null_select(); cs.enumerate_select(); cs.iv_select(); cs.psi_select()
corr_stay, corr_data, corr_matrix = cs.corr_select()
cols_chi2, chi2_df = cs.chi2_select()
ml_cols, ml_imp = cs.ml_select()
selected_cols, vif_detail = cs.vif_select()

# 4. 切分训练/测试集
from sklearn.model_selection import train_test_split
train, test = train_test_split(woe_df, test_size=0.2, random_state=666, shuffle=True)

# 5. 训练 + 调参 + 报告
mt = binary_model(train=train, test=test, cols=selected_cols, target='target',
                  model_path='step6_binary_model',
                  train_data_path='step6_binary_model/datas', save=True)
mt.lgbm_fit(num_leaves=31, learning_rate=0.1, n_estimators=100, verbose=-1)
para_dic = mt.para_adj_gs(paras={'num_leaves':[15,31,63],'learning_rate':[0.05,0.1,0.2]},
                          score_type='roc_auc', cv=2)
bins_rpt_all, confusion_matrix, fea_bins_report, fea_report = mt.report(
    group=20, mth_col='mth', base_mth=202401, bins_type='all')
```

---

## 1. 测试数据生成 `models_for_try`

### 1.1 `get_random_data(min_edge, max_edge, num, mth_cnt)`

生成一张带月份、9 个特征、二分类目标、随机缺失值的随机数据表，用于测试全流程。

**参数**（全部必填，无默认值）：

| 参数 | 类型 | 说明 |
|---|---|---|
| `min_edge` | int | 整数特征随机下界（含） |
| `max_edge` | int | 整数特征随机上界（不含） |
| `num` | int | 数据行数 |
| `mth_cnt` | int | 月份个数，月份在 `[202401, 202401+mth_cnt)` 内随机 |

**返回**：`pandas.DataFrame`，列结构（列顺序即此顺序）：

| 列名 | 类型 | 说明 |
|---|---|---|
| `id` | str | 行索引转字符串 |
| `mth` | int | 月份，如 202401 |
| `target` | int | 0/1 目标变量（由 9 个特征标准化后线性加权过 sigmoid×0.9 概率二项采样，坏样本偏少） |
| `col1`,`col2` | int | randint(min_edge, max_edge) |
| `col3`,`col4`,`col5` | float | rand 0~1 |
| `col6`,`col7` | int | randint(min_edge, max_edge)，含 20% NaN |
| `col8`,`col9` | int | randint(1,10)，含 20% NaN |
| `col10` | str | 从 A/B/C/D 随机抽 2 个字母逗号拼接，如 `'A,C'` |

> `col4`~`col9` 各随机引入 20% 缺失值（NaN）。

```python
df = get_random_data(0, 100, 10000, 6)
```

---

## 2. 数据预处理 `preprocess`

### 2.1 类 `cols_operate` —— 列变换工具集

```python
cols_operate(df, mean_key, col, date_col,
             explode_method=",", col_split_method=",",
             date_type='%Y%m%d', jieba_method=",")
```

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `df` | DataFrame | 必填 | 输入数据集 |
| `mean_key` | str | 必填 | 主键列名（`col_explode` 使用） |
| `col` | str | 必填 | 被操作的列名 |
| `date_col` | str | 必填 | 日期列名（日期类方法使用） |
| `explode_method` | str | `","` | `col_explode` 炸裂分隔符 |
| `col_split_method` | str | `","` | `col_to_cols` 分列分隔符 |
| `date_type` | str | `'%Y%m%d'` | `date_col_trans` 目标日期格式（strftime 格式） |
| `jieba_method` | str | `","` | `jieba_trans` 分词结果连接符 |

所有方法基于构造时 df 的副本运算，**返回新 DataFrame，不改原 df**。

| 方法 | 功能 | 返回 |
|---|---|---|
| `col_explode()` | 将 `col` 按 `explode_method` 分隔符一行炸裂为多行，`mean_key` 作主键复制 | DataFrame（`mean_key` + `col` 两列） |
| `col_to_T()` | 将 `col` 每行值转置为列，以单元格内容为新列名，原 `col` 行删除 | DataFrame |
| `col_to_cols()` | 将 `col` 按 `col_split_method` 拆成多列，新列名 `col1`,`col2`,...，原列删除 | DataFrame |
| `date_col_trans()` | `date_col` 转 datetime 后按 `date_type` 格式化为字符串 | DataFrame |
| `date_mth_year()` | 从 `date_col`（YYYYMMDD 类）截取前 6/4 位，新增 `mth`、`year` 两列（str） | DataFrame |
| `jieba_trans()` | 对 `col` 每行 jieba 分词，用 `jieba_method` 连接 | DataFrame |

```python
co = cols_operate(df=df, mean_key='id', col='col10', date_col='trade_date')
ex = co.col_explode()          # col10 'A,C' → 两行
ty = co.date_mth_year()        # 新增 mth/year 列
```

### 2.2 类 `desc_df` —— 全量特征描述统计

```python
desc_df(df, cols, col=None, cat_cols=[], path='step0_desc_result')
```

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `df` | DataFrame | 必填 | 输入数据集 |
| `cols` | list[str] | 必填 | 要统计的特征列 |
| `col` | str | `None` | 单变量分析时指定（内部方法用） |
| `cat_cols` | list[str] | `[]` | 手动指定离散特征列；`[]` 时自动识别（数值 dtype 且 unique>10 → 连续，否则离散） |
| `path` | str | `'step0_desc_result'` | 输出目录 |

| 方法 | 功能 | 返回 |
|---|---|---|
| `get_rpt()` | 对 `cols` 逐列做连续/离散描述统计 | `(num_report, cat_report)` 两个 DataFrame |
| `get_kind()` | 判断当前 `self.col` 是 `'num'` 还是 `'cat'` | str |
| `numeric_desc()` | 连续变量统计 | 单行 DataFrame，列：`col_name,type,n_unique,missing_rate,mean,std,cv,skew,median,25%,75%,min,max` |
| `categorical_desc()` | 离散变量统计 | DataFrame，列：`col_name,value,type,n_unique,missing_rate,index,ratio` |

**输出文件**（`path/`）：`desc_num_rpt.xlsx`（连续变量报告）、`desc_cat_rpt.xlsx`（离散变量报告）

```python
desc = desc_df(df=df, cols=cols, cat_cols=['col9'])
num_report, cat_report = desc.get_rpt()
```

### 2.3 类 `desc_col(desc_df)` —— 单变量深度分析卡片

```python
desc_col(df, target, col, cols, cat_cols=[], corr_threshold=0.5,
         path='step0_single_col_desc_result')
```

> ⚠️ **参数顺序注意**：`df, target, col, cols`（target 在 col 前，cols 在最后）。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `df` | DataFrame | 必填 | 输入数据集 |
| `target` | str | 必填 | 目标列名（有监督分析用） |
| `col` | str | 必填 | 要分析的单个特征列名 |
| `cols` | list[str] | 必填 | 全部特征列（相关性分析时计算 `col` 与它们的相关系数） |
| `cat_cols` | list[str] | `[]` | 手动指定离散特征 |
| `corr_threshold` | float | `0.5` | 相关性筛选阈值（spearman 绝对值大于它才输出） |
| `path` | str | `'step0_single_col_desc_result'` | 输出目录 |

| 方法 | 功能 | 输出文件 |
|---|---|---|
| `desc_()` | 描述统计（连续变量另画 9 分位条形图） | `<col>_quantile.png`、`<col>_desc.xlsx` |
| `relative_()` | 与 `cols` 的 spearman 相关性，输出超阈值变量 | `<col>_corr.xlsx`（列：`cols_corred_with_<col>, corr_value`） |
| `supervised_()` | 有监督分箱（连续→eq_cnt / 离散→cat_bin，group=10，nan=-9999），打印 IV | `<col>_woe.xlsx`、`<col>_woe.png` |
| `easy_od(how='whisker')` | 异常值检测，`how` 取 `'whisker'`（1.5 倍 IQR）或 `'3sigma'` | 无文件，返回 `(lower, upper, 离群点占比)` |
| `feat_card()` | **一键全做**：desc_ + relative_ + supervised_ + easy_od（连续变量另画箱线图/直方图） | 上述全部 + `<col>_outlier.png`、`<col>_distribution.png` |

```python
dc = desc_col(df=df, target='target', col='col1', cols=cols, corr_threshold=0.5)
dc.feat_card()      # 一般只用这一个方法即可输出全部结果
```

---

## 3. 特征编码 `cols_encode`

### 3.1 类 `cols_encode` —— 7 种特征编码

```python
cols_encode(df, cols, sc_model=StandardScaler(), mm_model=MinMaxScaler(),
            bins_type='eq_cnt', group=10, target='target', nan=-9999,
            path='step1_cols_encode', adj_bin=False, min_group=2)
```

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `df` | DataFrame | 必填 | 输入数据集 |
| `cols` | list[str] | 必填 | 要编码的特征列 |
| `sc_model` | sklearn scaler | `StandardScaler()` | Z-score 标准化器 |
| `mm_model` | sklearn scaler | `MinMaxScaler()` | 归一化器 |
| `bins_type` | str | `'eq_cnt'` | 分箱编码用的分箱方式：`eq_cnt`/`eq_distance`/`deci_tree_bin`/`chi2_bin`/`cat_bin` |
| `group` | int | `10` | 分箱个数 |
| `target` | str | `'target'` | 目标列名 |
| `nan` | 数值 | `-9999` | 缺失值填充值 |
| `path` | str | `'step1_cols_encode'` | 输出目录 |
| `adj_bin` | bool | `False` | 是否按坏率/IV 合并分箱（仅分箱编码用） |
| `min_group` | int | `2` | 合并分箱最小箱数 |

> 内部自动构造一个 `bins_model`（mth_col=None）供分箱/WOE 编码使用，因此 `bins_to_num`/`data_to_woe` 还会在 `path/` 额外产生 `bins_rpt_num_<bins_type>.xlsx`、`woe_mapping_<bins_type>.pkl`。

| 方法 | 功能 | 返回 | 输出文件（`path/`） |
|---|---|---|---|
| `data_to_sigmoid()` | sigmoid 编码 `1/(1+e^-x)` | DataFrame | `sigmoid_encode_data.csv` |
| `data_to_sc()` | Z-score 标准化（保留 4 位小数） | DataFrame | `sc_encode_data.csv`、`z_score_model.pkl` |
| `data_to_minmax()` | MinMax 归一化（保留 4 位小数） | DataFrame | `minmax_encode_data.csv`、`min_max_model.pkl` |
| `data_to_softmax()` | 列内 softmax（保留 4 位小数） | DataFrame | `softmax_encode_data.csv` |
| `bins_to_num()` | 分箱后按箱排序映射为整数 1..N | `(data, details)`；`details={col: {区间: 序号}}` | `bins_encode_data.csv`、`bins_encode_detail.pkl` |
| `str_to_num()` | 字符串按出现频次排序映射为整数 1..N，缺失映射为 `nan` 参数值 | `(data, details)`；`details={col: {取值: 序号}}` | `count_encode_data.csv`、`count_encode_detail.pkl` |
| `data_to_woe()` | WOE 编码（先自动跑 comp_woe_iv） | `(woe_df, woe_dic)`；`woe_dic={col: {区间: woe值}}` | `woe_encode_detail.pkl` |

> 返回的 DataFrame 均为 **df 全量副本**（只覆盖 `cols` 列的编码值，其余列保留）。
> CSV 文件只含 `cols` 列（不含其他列）。

```python
ce = cols_encode(df=df, cols=cols, bins_type='eq_cnt', group=10,
                 target='target', nan=-9999)
datas = ce.data_to_sc()
datas, details = ce.bins_to_num()
datas, woe_dic = ce.data_to_woe()
```

---

## 4. 分箱 / WOE / IV / PSI `bins_model`

### 4.1 辅助函数 `chi_square_of_df_cols(data, col, target)`

对 `data[col]` 与 `data[target]` 做卡方独立性检验，返回 `(chi2, p)`。

### 4.2 类 `get_bins` —— 单变量分箱基础类

```python
get_bins(df, col, group, target, nan, path='step2_bins_result')
```

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `df` | DataFrame | 必填 | 输入数据集 |
| `col` | str | 必填 | 要分箱的列名 |
| `group` | int | 必填 | 目标分箱个数 |
| `target` | str | 必填 | 目标列名（决策树/卡方分箱用） |
| `nan` | 数值 | 必填 | 缺失值填充值（若列最小值 < nan 会打印警告） |
| `path` | str | `'step2_bins_result'` | 输出目录 |

**5 种分箱方法，统一返回 `(bin_edge, bins, bins_cnt)`**：

| 方法 | 签名 | 说明 |
|---|---|---|
| `eq_distance()` | 无参 | 等距分箱。边界首尾为 `-inf`/`inf`；有缺失时缺失单独成箱 |
| `eq_cnt()` | 无参 | 等频（分位数）分箱。空箱自动与相邻箱合并；有缺失时缺失单独成箱 `(-inf, nan]` |
| `deci_tree_bin(min_per=0.1)` | `min_per`: float，叶节点最小样本比例，默认 0.1 | 熵准则决策树分箱（`max_leaf_nodes=group`） |
| `chi2_bin(initial_group=20)` | `initial_group`: int，初始等频分组数，默认 20 | 卡方分箱：先等频切 initial_group 箱，再按卡方值最小相邻合并至 group 箱。**输出 `<col>_chi2_bin_detail.xlsx`**（合并过程：Bin_Low/Bin_High/Chi_Square/P_Value） |
| `cat_bin()` | 无参 | 离散变量分箱：每个取值一箱（缺失填 nan 后也作为取值） |

返回值说明：
- `bin_edge`: `list[float]`，分箱边界（含 ±inf / nan 边界）
- `bins`: `pd.Series`，每行对应的 `pd.Interval` 区间（cat_bin 为原值）
- `bins_cnt`: 每箱样本数（Series 或 DataFrame）

### 4.3 类 `bins_model(get_bins)` —— 批量分箱报告 / WOE / IV / PSI / 画图

```python
bins_model(df, cols, group, target, nan, bins_type,
           col=None, mth_col=None, base_mth=None, cmp_mth=None,
           chi2_initial_group=20, adj_bin=False, cat_cols=False, min_group=2,
           bad_rate_adj=0.01, good_rate_adj=0.01, iv_adj=0.01,
           map_bins_list=None, path='step2_bins_result')
```

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `df` | DataFrame | 必填 | 输入数据集 |
| `cols` | list[str] | 必填 | 特征列列表（批量分析） |
| `group` | int | 必填 | 目标分箱个数 |
| `target` | str | 必填 | 目标列名 |
| `nan` | 数值 | 必填 | 缺失值填充值 |
| `bins_type` | str | 必填 | 分箱方式：`eq_cnt` / `eq_distance` / `deci_tree_bin` / `chi2_bin` / `cat_bin` |
| `col` | str | `None` | 单变量分析时的列名（`bins_rpt`/`plot_col_rpt` 用） |
| `mth_col` | str | `None` | 月份列名（PSI 用） |
| `base_mth` | int/str | `None` | 基准月份，如 `202401`（PSI 用，建议 int） |
| `cmp_mth` | int/str | `None` | 比较月份，如 `202403`（`get_psi` 用） |
| `chi2_initial_group` | int | `20` | 卡方分箱初始箱数（应 ≥ group，否则打印警告） |
| `adj_bin` | bool | `False` | True 时按坏率/好率/iv_bin 阈值迭代合并分箱 |
| `cat_cols` | bool | `False` | 是否离散特征（只影响输出文件名 cat/num） |
| `min_group` | int | `2` | adj_bin 合并时的最小箱数 |
| `bad_rate_adj` | float | `0.01` | 合并阈值：箱坏率低于它则与后箱合并 |
| `good_rate_adj` | float | `0.01` | 合并阈值：箱好率低于它则合并 |
| `iv_adj` | float | `0.01` | 合并阈值：箱 iv_bin 低于它则合并 |
| `map_bins_list` | list | `None` | 自定义分箱边界列表（配 `map_bins_rpt`，⚠️ 该方法引用未定义全局变量 cols，存在 bug，不建议使用） |
| `path` | str | `'step2_bins_result'` | 输出目录 |

#### 方法详解

**(1) `bins_rpt()` —— 单变量分箱报告（需先设 col）**

- 返回 `(woe_final, iv)`：
  - `woe_final`: DataFrame，列 = `bucket, col_name, bad_cnt, good_cnt, total_cnt, bad_rate, good_rate, badattr, goodattr, badattr_cum, goodattr_cum, woe, iv_bin, ks_bin, lift, iv, ks, bins_type`
  - `iv`: float，该变量 IV 值
- `adj_bin=True` 时返回合并后的 `merge_df`（同结构）；合并规则：`bad_rate<bad_rate_adj` 或 `good_rate<good_rate_adj` 或 `iv_bin<iv_adj` 或空箱 → 与相邻箱合并，迭代至箱数 ≤ `min_group` 或无法再合并（最多 100 轮）
- ⚠️ 若该列唯一值只有 1 个：从 `self.cols` 中剔除该列，返回 `(空DataFrame, 0)`

**(2) `comp_woe_iv()` —— 批量分箱报告 + IV**

- 对 `cols` 逐列跑 `bins_rpt()`
- 返回 `(woe_data, iv_data)`：
  - `woe_data`: 所有变量的分箱报告纵向拼接（列同 `bins_rpt`）
  - `iv_data`: DataFrame，列 = `var, iv_value`
- 结果缓存到 `self.cols_bins_rpt` / `self.cols_iv_data`（供 `data_to_woe` 复用）
- **输出文件**（`path/`，按 cat_cols/adj_bin 组合命名）：
  - `bins_rpt_num_<bins_type>.xlsx`（连续+不合并）
  - `bins_rpt_adj_num_<bins_type>.xlsx`（连续+合并）
  - `bins_rpt_cat_<bins_type>.xlsx` / `bins_rpt_adj_cat_<bins_type>.xlsx`（离散）

**(3) `data_to_woe()` —— 全量数据 WOE 转换**

- 若未跑过 `comp_woe_iv` 会自动先跑
- 返回 `(woedf, woe_mapping)`：
  - `woedf`: df 副本，`cols` 列值替换为 WOE 值（先 fillna(nan) 再映射）
  - `woe_mapping`: `{col: {pd.Interval区间: woe值}}` 字典
- **输出文件**：`woe_mapping_<bins_type>.pkl`

**(4) `get_psi()` —— 两月 PSI（需 mth_col/base_mth/cmp_mth）**

- 基准月按 bins_type 分箱，比较月套用同一边界，计算 `PSI = Σ(base%-cmp%)·ln(base%/cmp%)`（分母加 0.00001 防零）
- 返回 `psi_data`: DataFrame，列 = `var, psi, base_grp_pct, cmp_grp_cnt, cmp_grp_pct`（后三列为每箱占比/数量序列）
- **输出文件**：`psi_single_rpt_<bins_type><base_mth>_<cmp_mth>.xlsx`

**(5) `psi_mth_avg()` —— PSI 月均值（需 mth_col/base_mth）**

- 以 `base_mth` 为基准，对数据中其余每个月份逐一跑 `get_psi()`，取各月 PSI 均值
- 返回 `psi_avg_data`: DataFrame，列 = `var, psi`
- **输出文件**：`psi_avg_rpt_<bins_type>.xlsx`（另每月各生成一份 psi_single_rpt）

**(6) `plot_col_rpt(show=False)` —— 单变量 4 图（需先设 col）**

- 依次画 bad_rate / woe / lift / ks（好坏累计曲线）并保存：
  - `<col>_bad_rate_<bins_type>.png`、`<col>_woe_iv_<bins_type>.png`、`<col>_lift_<bins_type>.png`、`<col>_ks_<bins_type>.png`
- `show=True` 时最后 `plt.show()`

**(7) `plot_cols_rpt(show=False)` —— 对 cols 全部变量循环调用 `plot_col_rpt`**

**(8) 继承自 get_bins 的 5 个分箱方法**（`eq_cnt`/`eq_distance`/`deci_tree_bin`/`chi2_bin`/`cat_bin`）也可直接调用。

**(9) `map_bins_rpt()` —— 按自定义边界出报告（⚠️ 勿用）**

- 无参数，配合构造参数 `map_bins_list`（每个特征一份自定义分箱边界 list）使用，返回 `(woe_data, iv_data)`
- ⚠️ 源码第一行 `if len(cols)!=len(self.map_bins_list)` 引用了未定义的全局变量 `cols`（应为 `self.cols`），除非恰好存在同名全局变量否则必然 NameError。**请改用 `bins_rpt`/`comp_woe_iv`**

```python
bm = bins_model(df=df, cols=cols, group=10, target='target', nan=-9999,
                bins_type='eq_cnt', mth_col='mth', base_mth=202401, cmp_mth=202403,
                col='col7', adj_bin=True, cat_cols=False, path='step2_bins_result')
bin_edge, bins, bins_cnt = bm.eq_cnt()     # 单变量分箱
woe, iv = bm.bins_rpt()                    # 单变量报告
woe_data, iv_data = bm.comp_woe_iv()       # 批量报告
woe_df, woe_detail = bm.data_to_woe()      # WOE 转换
psi_data = bm.get_psi()                    # 两月 PSI
psi_avg_data = bm.psi_mth_avg()            # PSI 月均值
bm.plot_col_rpt(show=False)
bm.plot_cols_rpt(show=False)
```

---

## 5. 特征选择 `cols_select`

### 5.1 类 `cols_select` —— 十种筛选方法（链式流水线）

```python
cols_select(df, cols, target, psi_data=None, iv_data=None,
            null_pct=0.95, enu_cnt=1, enu_pct=0.95,
            psi_thres=0.1, iv_thres=0.01, corr_method='spearman', corr_thres=0.8,
            chi2_p_value_thres=0.5, lg_method='recursion', lg_C=0.1,
            ml_method='lgb', boot_method='lgb', boot_thres=100,
            vif_thres=10, nan=0, path='step4_cols_select')
```

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `df` | DataFrame | 必填 | 输入数据集 |
| `cols` | list[str] | 必填 | 待筛选特征列 |
| `target` | str | 必填 | 目标列名 |
| `psi_data` | DataFrame | `None` | `bins_model.psi_mth_avg()` 的返回值（`psi_select` 必需） |
| `iv_data` | DataFrame | `None` | `bins_model.comp_woe_iv()` 返回的 iv_data（`iv_select`/`corr_select` 必需） |
| `null_pct` | float | `0.95` | 缺失率高于它的变量剔除 |
| `enu_cnt` | int | `1` | unique 值个数 ≤ 它的变量剔除 |
| `enu_pct` | float | `0.95` | 单一取值占比 ≥ 它的变量剔除 |
| `psi_thres` | float | `0.1` | PSI ≥ 它的变量剔除 |
| `iv_thres` | float | `0.01` | IV ≤ 它的变量剔除 |
| `corr_method` | str | `'spearman'` | 相关系数算法：`spearman`/`pearson`/`kendall` |
| `corr_thres` | float | `0.8` | 相关系数绝对值 ≥ 它时，剔除两者中 IV 较小者 |
| `chi2_p_value_thres` | float | `0.5` | 卡方 p 值 ≥ 它的变量剔除 |
| `lg_method` | str | `'recursion'` | 逻辑回归筛选方式：`recursion`（RFE 递归）/ `l1penalty`（L1 正则） |
| `lg_C` | float | `0.1` | 逻辑回归正则化倒数（越小正则越强，仅 l1penalty 用） |
| `ml_method` | str | `'lgb'` | 机器学习筛选模型：`lgb`/`xgb`/`rdf` |
| `boot_method` | str | `'lgb'` | bootstrap 筛选模型：`lgb`/`xgb`/`rdf` |
| `boot_thres` | float | `100` | bootstrap 重要性均值 ≤ 它的变量剔除 |
| `vif_thres` | float | `10` | VIF 上限，迭代剔除最大 VIF 直到全部 ≤ 它 |
| `nan` | 数值 | `0` | 卡方/LR/VIF 筛选前的缺失填充值 |
| `path` | str | `'step4_cols_select'` | 输出目录 |

> ⚠️ 源码 docstring 写 lg_C 默认 0.001，**实际默认 0.1**。

**链式机制**：所有方法共享 `self.cols_s`（初始=cols），每个方法在**上一步结果**上继续筛，并更新 `self.cols_s`。按顺序调用即成流水线；每步都返回当前剩余列。

| 方法 | 功能 | 返回 | 输出文件（`path/`） |
|---|---|---|---|
| `null_select()` | 缺失率筛选 | 剩余列 list | `null_pct_drop_detail.pkl`（被剔列及缺失率 dict）、`null_pct_select_cols.pkl` |
| `enumerate_select()` | 枚举值筛选 | 剩余列 list | `enu_drop_detail.pkl`、`enu_select_cols.pkl` |
| `iv_select()` | IV 筛选（需 iv_data） | 剩余列 list | `iv_select_cols.pkl`、`iv_drop_detail.xlsx` |
| `psi_select()` | PSI 筛选（需 psi_data） | 剩余列 list | `psi_drop_detail.xlsx`、`psi_select_cols.pkl` |
| `corr_select()` | 相关性筛选（需 iv_data，高相关剔 IV 低者） | `(corr_stay, corr_data, corr_matrix)`：剩余列、变量对明细（var1/var2/corr_value/z_score/p_value/iv_diff）、相关矩阵 | `corr_select_cols.pkl`、`corr_all_detail.xlsx`、`corr_matrix_selected.xlsx`、`corr_matrix_selected.png`（热力图） |
| `chi2_select()` | 卡方检验筛选（先 MinMax 归一化，fillna(nan)） | `(cols_chi2, chi2_df)`：剩余列、全量卡方明细（var/chi2_value/p_value） | `chi2_select_cols.pkl`、`chi2_df.xlsx` |
| `logistic_select()` | 逻辑回归筛选（fillna(nan)；`recursion`=RFE 递归，`l1penalty`=L1+SelectFromModel） | 剩余列 list | recursion：`lg_RFE_model.pkl`、`lg_rfe_select_cols.pkl`；l1penalty：`lg_l1select_model.pkl`、`lg_l1_select_cols.pkl` |
| `ml_select()` | 树模型重要性筛选：重复训练（最多 100 轮）直到无重要性为 0 的变量 | `(cols_s, imp_data)`：剩余列、重要性明细（var/importances） | `ml_select_imp_data.xlsx`、`ml_select_cols.pkl` |
| `boostrap_select(num_iterations=10, frac=1)` | 有放回抽样 num_iterations 次训练，重要性均值 ≤ boot_thres 剔除。`frac`=每次抽样比例 | `(boos_cols, imp_data)`：剩余列、各轮重要性+均值明细 | `boot_select_imp_data.xlsx`、`boot_select_cols.pkl` |
| `vif_select()` | VIF 多重共线性筛选（fillna(nan)，迭代剔除最大 VIF） | `(cols_copy, filter_details)`：剩余列、逐步 VIF 明细（Step/Feature/VIF） | `vif_select_detail.xlsx`、`vif_select_cols.pkl` |

> 每步结果同时缓存为属性：`null_cols`/`enumerate_cols`/`iv_cols`/`psi_cols`/`corr_cols`/`chi2_cols`/`lr_cols`/`ml_cols`/`boos_cols`/`vif_cols`。
> IV 参考阈值：<0.03 无预测能力；0.03~0.09 低；0.1~0.29 中；0.3~0.49 强；>0.5 极强。

```python
cs = cols_select(df=df, cols=cols, target='target',
                 psi_data=psi_avg_data, iv_data=iv_data)
null_cols = cs.null_select()
enumerate_cols = cs.enumerate_select()
iv_cols = cs.iv_select()
psi_cols = cs.psi_select()
corr_stay, corr_data, corr_matrix = cs.corr_select()
cols_chi2, chi2_df = cs.chi2_select()
lg_cols = cs.logistic_select()
ml_cols, ml_imp = cs.ml_select()
boot_cols, boot_imp_data = cs.boostrap_select()
vif_cols, vif_detail = cs.vif_select()
```

### 5.2 类 `cols_select_js` —— 递归特征选择（自动定最优特征数）

```python
cols_select_js(train, test, cols, target, method='lgb',
               recursion_num=30, stay_pct=0.95, path='step5_cols_js')
```

> ⚠️ **与源码 docstring 的差异**：docstring 示例为 `cols_select_js(df, cols, target, method='xgb', shuffle=..., random_state=..., test_size=...)`，**已过时**。真实签名接收已切分好的 `train`/`test` 两个 DataFrame，无 shuffle/random_state/test_size 参数；method 默认 `'lgb'`（docstring 写 xgb）。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `train` | DataFrame | 必填 | 训练集（含 cols 和 target） |
| `test` | DataFrame | 必填 | 测试集（含 cols 和 target） |
| `cols` | list[str] | 必填 | 候选特征列 |
| `target` | str | 必填 | 目标列名 |
| `method` | str | `'lgb'` | 模型：`lgb` / `xgb` |
| `recursion_num` | int | `30` | 递归迭代次数。**必须 ≥ 5**（结尾绘图代码固定访问 ks_test 前 5 名的 `top[0]`~`top[4]`，迭代不足 5 轮会 KeyError） |
| `stay_pct` | float | `0.95` | 每轮保留重要性非 0 变量中排名前 stay_pct 比例的变量 |
| `path` | str | `'step5_cols_js'` | 输出目录 |

**方法 `recursion_select(*args, **kwargs)`**：
- `*args/**kwargs` 透传给 `LGBMClassifier` / `XGBClassifier` 构造器（如 `num_leaves=31, n_estimators=100, verbose=-1`）
- 每轮：训练 → 在 test/train 上评估 acc/ks/auc → 按重要性保留前 stay_pct → 下一轮
- 最优轮选择逻辑：ks_test 前 5 → auc_change_pct 前 3 → ks_change_pct 前 2 → 取其中 run_cnt 最大（最晚）的一轮
- **返回 `(js_data, cols_detail, js_cols)`**：
  - `js_data`: DataFrame，列 = `run_cnt, staynum, acc, ks_test, auc_test, ks_train, auc_train, ks_change_pct, auc_change_pct, ks_train_test_dif`
  - `cols_detail`: list[list]，每轮入模变量
  - `js_cols`: list，最优轮变量列表
- **输出文件**（`path/`）：`<method>_js.png`（4 联曲线图）、`js_recu_data_<method>.xlsx`、`js_cols_detail_<method>.pkl`、`js_select_cols_<method>.pkl`

```python
js = cols_select_js(train=train, test=test, cols=selected_cols, target='target',
                    method='lgb', recursion_num=30, stay_pct=0.95, path='step5_cols_js')
js_data, cols_detail, js_cols = js.recursion_select(num_leaves=31, n_estimators=100, verbose=-1)
```

---

## 6. 模型训练 `model_training`

### 6.1 类 `binary_model` —— 二分类模型训练与评估报告

```python
binary_model(train, test, cols, target,
             model_path='step6_binary_model',
             train_data_path='step6_binary_model/datas', save=False)
```

> ⚠️ **与源码 docstring 的差异**：docstring 示例为 `binary_model(df=..., shuffle=..., random_state=..., test_size=...)`，**已过时**。真实签名直接接收切分好的 `train`/`test`；如需从单表切分，用下方 `train_test_split()` 方法。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `train` | DataFrame | 必填 | 训练集（必须含 `cols` 各列和 `target` 列） |
| `test` | DataFrame | 必填 | 测试集（同上） |
| `cols` | list[str] | 必填 | 入模特征列 |
| `target` | str | 必填 | 目标列名（0/1） |
| `model_path` | str | `'step6_binary_model'` | 模型/报告输出目录 |
| `train_data_path` | str | `'step6_binary_model/datas'` | 数据集 csv 输出目录 |
| `save` | bool | `False` | True 时立即把 train/test 的 `cols` 列存为 csv |

内部属性：`self.df = concat(train, test)`（全量）；训练后产生 `self.clf`（模型对象）、`self.model_type`（`lr`/`xgb`/`lgbm`/`keras`/`svm`/`rdf`）、`self.adj`（`noadj`/`gs_adj`/`bs_adj`）、`self.cols_weight`（系数/重要性表）。

#### 方法详解

**(0) `train_test_split(df, shuffle, random_state, test_size)`** —— 从单表切分（构造时未传 train/test 的补救方式）：切分并覆盖 `self.xtrain/self.xtest`，同时保存 `train.csv`/`test.csv`。

**(1) 六个训练方法**（参数全部透传给对应 sklearn/xgb/lgbm/keras 构造器；训练后自动保存系数/重要性表和模型文件）：

| 方法 | 底层模型 | 常用参数示例 | 输出文件（`model_path/`） | model_type |
|---|---|---|---|---|
| `lr_fit(*args, **kwargs)` | `LogisticRegression` | `penalty='l2', C=1, solver='saga', max_iter=100` | `lr_coef.xlsx`（col_name/coef）、`lr_model.pkl` | `lr` |
| `xgb_fit(*args, **kwargs)` | `XGBClassifier` | `n_estimators=100, max_depth=5, learning_rate=0.1, subsample=0.8, colsample_bytree=0.8, reg_alpha=0, reg_lambda=1` | `xgb_imp.xlsx`（col_name/importances）、`xgb_model.pkl` | `xgb` |
| `lgbm_fit(*args, **kwargs)` | `LGBMClassifier` | `num_leaves=31, max_depth=5, learning_rate=0.1, n_estimators=100, min_child_weight=1, subsample=0.8, colsample_bytree=0.8, reg_alpha=0, reg_lambda=0, verbose=-1` | `lgbm_imp.xlsx`、`lgbm_model.pkl` | `lgbm` |
| `mlp_fit(epochs=5, batch_size=100, validation_split=0.5, metrics=[], loss='binary_crossentropy', optimizer='adam')` | keras Sequential（64→32→1，relu/relu/sigmoid） | 如 `epochs=30, metrics=['AUC']` | `mlp_weight.csv`（col_name/weight 首层权重绝对值列和）、`mlp_model.h5` | `keras` |
| `svm_fit(*args, **kwargs)` | `SVC(kernel='linear', probability=True, ...)` | 追加参数透传 | `svm_imp.xlsx`（系数）、`svm_model.pkl` | `svm` |
| `rdf_fit(*args, **kwargs)` | `RandomForestClassifier` | `n_estimators=100, n_jobs=-1, random_state=666` | `rdf_imp.xlsx`、`rdf_model.pkl` | `rdf` |

> ⚠️ mlp_fit 真实默认值为 `epochs=5, batch_size=100, validation_split=0.5`（docstring 写的 30/10/0.2 已过时）。

**(2) `para_adj_gs(paras, score_type='roc_auc', cv=2)` —— 网格搜索调参**

- `paras`: dict，参数网格，如 `{'learning_rate':[0.05,0.1,0.2],'num_leaves':[15,31,63]}`
- 对**当前 self.clf** 做 GridSearchCV → 打印 best_params_ → 用最优参数重训 self.clf
- 返回 `para_dic`（最优参数字典）
- 输出：`<model_type>_gs_adj.pkl`（搜索器）、`<model_type>_gs_adj_model_final.pkl`（最优模型）；`self.adj='gs_adj'`

**(3) `para_adj_bs(paras, score_type='roc_auc', cv=2)` —— 贝叶斯调参**

- 同上，底层为 `BayesSearchCV`（scikit-optimize）
- 输出：`<model_type>_bs_adj.pkl`、`<model_type>_bs_adj_model_final.pkl`；`self.adj='bs_adj'`

**(4) `report(group, mth_col, base_mth, bins_type='all', cols_bins_rpt='')` —— 完整评估报告**

> ⚠️ `group`、`mth_col`、`base_mth` 均为**必填位置参数**（docstring 示例 `report(group=30, bins_type='eq_cnt')` 缺 mth_col/base_mth，已过时）。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `group` | int | 必填 | 评分概率分组个数 |
| `mth_col` | str | 必填 | 月份列名（逐月效果 + 特征 PSI 用；数据必须含此列） |
| `base_mth` | int/str | 必填 | PSI 基准月份 |
| `bins_type` | str | `'all'` | 评分分组方式：`all`=等频+等距+逐月、`eq_cnt`=等频+逐月、`eq_distance`=等距+逐月 |
| `cols_bins_rpt` | DataFrame | `''` | 可选，传入之前 `comp_woe_iv` 的 woe_data 复用特征分箱报告；为空则内部重算 |

报告内容：
1. 总体指标（test/train/all 三份）：accuracy、precision、recall、F1、AUC、KS
2. 评分卡分数：`score = A - B·ln(Odds)`，P=660、Q=20、PDO=50（Odds=(1-p)·1/p），负分截断为 0
3. 评分分箱报告（每箱的 bad_rate/woe/lift/ks/auc/逐月等）
4. 入模特征报告：iv、ks、psi、权重/重要性、vif、缺失率、0 值率
5. 画图：KS 曲线、ROC 曲线、LIFT 曲线、概率分布、评分分布（均 test/train/all 三联图）

**返回 `(bins_rpt_all, confusion_matrix, fea_bins_report, fea_report)`**（4 个 DataFrame）：
- `bins_rpt_all`: 评分分箱报告，列 = `data_type, bins_type, bucket, col_name, total_cnt, bad_cnt, good_cnt, pred_bad, bad_rate, good_rate, badattr, goodattr, badattr_cum, goodattr_cum, accuracy, precision, recall, woe, f1, ks_bin, ks_grp, auc_grp, lift, ks, auc`
- `confusion_matrix`: 总体指标表，列 = `type, accuracy, precision, recall, F1, auc, ks`（行：test/train/all）
- `fea_bins_report`: 入模特征分箱报告
- `fea_report`: 入模特征效果表，列 = `col_name, importances(权重), iv, ks, psi, missing_rate, 0_rate, vif`

**输出文件**：
- `model_path/`：`confusion_matrix_<model_type>_<adj>.xlsx`、`bins_rpt_<bins_type>_<model_type>_<adj>_<group>_bins.xlsx`、**`<model_type>_<group>bins_full_report.xlsx`**（总报告，4 个 sheet：model_report/score_bins_report/feature_bins_report/feature_report，带样式和插图）、`ks_curve_<model_type>_<adj>.png`、`roc_curve_<model_type>_<adj>.png`、`lift_curve_<model_type>_<adj>.png`、`prob_distribution_<model_type>_<adj>.png`、`score_distribution_<model_type>_<adj>.png`
- `train_data_path/`：`xtest_pred.csv`、`xtrain_pred.csv`、`all_pred.csv`（列：y_pred_prob/y_pred/score）
- `model_path/bins_report/`：特征分箱/PSI 中间文件（bins_rpt_num_eq_cnt.xlsx、psi_avg_rpt_eq_cnt.xlsx、psi_single_rpt_*.xlsx）
- `model_path/` 另含 VIF 中间文件：`vif_select_detail.xlsx`、`vif_select_cols.pkl`（report 内部调用 cols_select.vif_select 产生）
- 副作用：给 `self.xtrain/self.xtest/self.df` 追加 `y_pred_prob`、`y_pred`、`score` 三列

```python
mt = binary_model(train=train, test=test, cols=cols, target='target', save=True)
mt.lgbm_fit(num_leaves=31, learning_rate=0.1, n_estimators=100, verbose=-1)
para_dic = mt.para_adj_gs(paras={'num_leaves':[15,31,63]}, score_type='roc_auc', cv=2)
bins_rpt, confusion_matrix, fea_bins_report, fea_report = mt.report(
    group=20, mth_col='mth', base_mth=202401, bins_type='all')
```

### 6.2 类 `uns_model` —— 无监督学习（PCA / KMeans）

```python
uns_model(df, cols, target=None, mean_key=[], path='step6_uns_model')
```

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `df` | DataFrame | 必填 | 输入数据集（**必须先处理缺失值**，如 `df.fillna(0)`） |
| `cols` | list[str] | 必填 | 特征列 |
| `target` | str | `None` | 目标列名（pca 结果会带上该列，可选） |
| `mean_key` | str 或 list | `[]` | 主键列（pca 结果会带上） |
| `path` | str | `'step6_uns_model'` | 输出目录 |

| 方法 | 功能 | 返回 | 输出文件（`path/`） |
|---|---|---|---|
| `pca(var_ratio_cumsum=0.8)` | 主成分分析：自增主成分个数直到累计方差解释率 ≥ 阈值；得分=主成分×方差解释率加权 | `(pca_cols, pca_data, pca_detail)`：主成分列名 `['F1','F2',...]`、得分表（mean_key+F列+target）、成分系数矩阵（行=F列，列=原特征） | `pca_cols.pkl`、`pca_data.csv`、`pca_model.pkl`、`pca_details.csv`、`pca_cols_corr.xlsx` |
| `get_keams_cluster()` | 聚类数评估：k=2~29 计算 SSE（肘部法）和轮廓系数并画图 | 无 | `SSE肘部法评估.png`、`轮廓系数评估.png` |
| `kmeans(n_clusters)` | KMeans 聚类（random_state=666） | `(data, kmeans_detail)`：原 df 副本+`cluster_labels` 列、聚类中心表（行=类别，列=特征均值） | `cluster_labels.pkl`、`kmeans_center.xlsx` |

```python
um = uns_model(df.fillna(0), cols, target='target', mean_key='id', path='step6_uns_model')
pca_cols, pca_data, pca_detail = um.pca(var_ratio_cumsum=0.8)
um.get_keams_cluster()
data, kmeans_detail = um.kmeans(n_clusters=10)
```

### 6.3 类 `linear_model` —— OLS/Logit 回归与工具变量回归

```python
linear_model(df, cols, iv_target, iv_col, target, path='step6_linear_model')
```

> ⚠️ **参数顺序注意**：`df, cols, iv_target, iv_col, target`。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `df` | DataFrame | 必填 | 输入数据集 |
| `cols` | list[str] | 必填 | 解释变量列 |
| `iv_target` | str | 必填 | 工具变量回归第一阶段的被解释变量（内生变量列） |
| `iv_col` | str | 必填 | 工具变量列 |
| `target` | str | 必填 | 被解释变量列 |
| `path` | str | `'step6_linear_model'` | 输出目录 |

| 方法 | 功能 | 返回 | 输出文件（`path/`） |
|---|---|---|---|
| `ols()` | 回归：`target` 只有 2 个唯一值时自动用 **logit**，否则 **OLS**；打印 summary，系数表加显著性标注（`***`p<0.01 / `**`p<0.05 / `*`p<0.1） | statsmodels 模型对象 | `<model_name>_model_base_estimate.xlsx`、`<model_name>_Significance_summary.xlsx`、`<model_name>_model.pkl`（model_name=logit 或 ols）；OLS 另输出 `ols_model_normal_distribution_estimate.xlsx`（正态性诊断表） |
| `IV()` | 两阶段工具变量回归：第一阶段 `iv_target ~ iv_col`，第二阶段 `target ~ 第一阶段预测值` | `(ols1, ols2)` 两个 statsmodels 模型 | `iv_ols1_model.pkl`、`iv_ols2_model.pkl` |

```python
lm = linear_model(df, cols, iv_target='col2', iv_col='col1', target='target',
                  path='step6_linear_model')
model = lm.ols()          # target 二分类时自动 logit
ols1, ols2 = lm.IV()
```

---

## 7. Linux 服务器连接 `con_linux`

> 目标服务器硬编码为 `192.168.10.100:22`。多段命令用英文分号 `;` 分隔，逐段执行并打印结果。

### 7.1 `con_linux(shell, user='chenbh17')`

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `shell` | str | 必填 | shell 命令，多段用 `;` 分隔（长度 ≤1 的段自动忽略） |
| `user` | str | `'chenbh17'` | 登录用户：`chenbh17` 或 `root` |

**返回**：`None`（结果直接 print）。

```python
con_linux('cd /home/chenbh17 && ls -l; df -h')
```

### 7.2 `data_trans_linux(local_loc=None, client_loc='/media/chenbh17/cbhssd/invest/data/to_hive.csv', method='put')`

SFTP 文件传输。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `local_loc` | str | `None` | 本地文件完整路径 |
| `client_loc` | str | `/media/chenbh17/cbhssd/invest/data/to_hive.csv` | 服务器端文件完整路径 |
| `method` | str | `'put'` | `put`=本地→服务器上传；`get`=服务器→本地下载 |

**返回**：`None`。

### 7.3 快捷函数

| 函数 | 功能 | 返回 |
|---|---|---|
| `jps()` | 远程执行 jps 查看 Java 进程 | None（打印） |
| `hadoop(type)` | `type='start'` 启动 / `'stop'` 关闭 hadoop | `'hadoop启动成功'` / `'hadoop关闭成功'` |
| `start_hive()` | 启动 hive metastore + hiveserver2（nohup 后台） | `'hive启动成功'` |

---

## 8. 数据库连接 `con_sql`

> SQL 多段用英文分号 `;` 分隔，逐段执行。长度 ≤1 的段自动忽略。

### 8.1 ClickHouse

连接硬编码：`192.168.10.100:8123`，用户 `admin`，库 `pro`。

**`chrun(sql)`** —— 执行不返回数据的 SQL（DDL/写入）：
- 返回 `'done'`

**`chdf(sql)`** —— 执行查询并返回数据：
- 1 段 → 返回 `DataFrame`；多段 → 返回 `list[DataFrame]`；无结果 → `None`

```python
chrun('create table pro.t1 (id Int64) engine=MergeTree order by id')
df = chdf('select * from pro.t1 limit 10')
```

### 8.2 MySQL

```python
con_mysql(sql=None, host='192.168.10.200', port=3306, user='hive',
          password='hive', database='dev', charset=None)
```

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `sql` | str | `None` | SQL 语句，多段 `;` 分隔 |
| `host` | str | `'192.168.10.200'` | 服务器地址 |
| `port` | int | `3306` | 端口 |
| `user` | str | `'hive'` | 用户名 |
| `password` | str | `'hive'` | 密码 |
| `database` | str | `'dev'` | 库名 |
| `charset` | str | `None` | 字符集 |

**返回**：1 个结果表 → `DataFrame`；多个 → `list[DataFrame]`；无查询结果或报错 → `None`。

### 8.3 Hive

```python
con_hive(sql=None, host='192.168.10.100', port=10000, auth='CUSTOM',
         database='pro', username='hive', password='hive')
```

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `sql` | str | `None` | Hive SQL，多段 `;` 分隔 |
| `host` | str | `'192.168.10.100'` | 服务器地址 |
| `port` | int | `10000` | hiveserver2 端口 |
| `auth` | str | `'CUSTOM'` | 认证方式 |
| `database` | str | `'pro'` | 库名 |
| `username` | str | `'hive'` | 用户名 |
| `password` | str | `'hive'` | 密码 |

行为：`load`/`insert` 开头的语句只执行不取数；其他语句执行后尝试 `pd.read_sql` 取结果（结果列名自动去掉 `表名.` 前缀）。
**返回**：1 个结果表 → `DataFrame`；多个 → `list[DataFrame]`；无结果 → `None`。

### 8.4 `get_create_table(...)` —— 由 DataFrame 生成 Hive 建表 SQL

```python
get_create_table(data, table_name, encoding=None, partition=None, bucket=None,
                 partition_col=None, bucket_col=None, bucket_num=10)
```

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `data` | DataFrame | 必填 | 用于推断列类型的数据 |
| `table_name` | str | 必填 | 表名，**必须含库名**，如 `'stock.cbh_try'` |
| `encoding` | str | `None` | `'GBK'` 时追加 SERDEPROPERTIES 编码设置；默认 utf-8 |
| `partition` | 任意真值 | `None` | 是否分区表 |
| `bucket` | 任意真值 | `None` | 是否分桶表 |
| `partition_col` | str | `None` | 分区列定义，格式 `'列名 类型'`，如 `'year string'` |
| `bucket_col` | str | `None` | 分桶列名 |
| `bucket_num` | int | `10` | 分桶数 |

类型映射：int64/int/int32 → `int`；float/float64/float32 → `double`；object → `string`。
**返回**：建表 SQL 字符串（含 `drop table if exists` + `create table ... row format delimited fields terminated by ','`）。

### 8.5 `to_hive(...)` —— DataFrame 一键导入 Hive

```python
to_hive(data, table_name, local_loc,
        shell_loc='/media/chenbh17/cbhssd/invest/data/',
        method='overwrite', encoding='UTF-8', partition=None, bucket=None,
        partition_col='year string', bucket_col=None, bucket_num=10)
```

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `data` | DataFrame | 必填 | 要导入的数据 |
| `table_name` | str | 必填 | 目标表名（含库名） |
| `local_loc` | str | 必填 | 本地 csv 临时路径，**文件名必须是 `to_hive.csv`**（服务器端去表头脚本硬编码该文件名） |
| `shell_loc` | str | `'/media/chenbh17/cbhssd/invest/data/'` | 服务器端文件中转目录 |
| `method` | str | `'overwrite'` | `overwrite`=删表重建覆盖；`append`=追加 |
| `encoding` | str | `'UTF-8'` | csv 编码 |
| `partition` | 任意真值 | `None` | 是否分区表；True 时按分区列 groupby 逐分区导入 |
| `bucket` | 任意真值 | `None` | 是否分桶 |
| `partition_col` | str | `'year string'` | 分区列定义（`'列名 类型'`） |
| `bucket_col` | str | `None` | 分桶列 |
| `bucket_num` | int | `10` | 分桶数 |

流程：本地存 csv → SFTP 上传 → 服务器去表头 → `load data local inpath` 入 Hive → 清理临时文件。
**返回**：`'数据表导入hive成功!'`（overwrite）或 `'数据表更新成功!'`（append）。

### 8.6 `rfms_sql(...)` —— RFMS 范式特征衍生（Hive 窗口函数）

```python
rfms_sql(data, cols, new_table, origin_table, day_list=[5, 20, 120, 250])
```

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `data` | DataFrame | 必填 | 样本数据（仅用于判断每列取值个数：=2 视为二分类列） |
| `cols` | list[str] | 必填 | 要衍生的特征列 |
| `new_table` | str | 必填 | 新表名（含库名） |
| `origin_table` | str | 必填 | 原表名（含库名，**必须含 stock_code/trade_date/mth/year 列**） |
| `day_list` | list[int] | `[5,20,120,250]` | 滚动窗口天数 |

衍生规则（按 `partition by stock_code order by trade_date` 滚动窗口 `rows between N-1 preceding and current row`）：
- 二分类列（unique=2）：`sum/avg/stddev` → 新列名 `<col>_l<N>d_<agg>`
- 其他列：`sum/avg/stddev/min/max`

**返回**：成功 `'rfms范式特征衍生操作完成，结果已保存在hive中，表名为：<new_table>'`；失败 `'con_hive报错'`。

---

## 9. 附录

### 9.1 输出文件速查总表

| 来源 | 默认目录 | 文件 |
|---|---|---|
| desc_df | step0_desc_result/ | desc_num_rpt.xlsx、desc_cat_rpt.xlsx |
| desc_col | step0_single_col_desc_result/ | `<col>_desc.xlsx`、`<col>_quantile.png`、`<col>_corr.xlsx`、`<col>_woe.xlsx`、`<col>_woe.png`、`<col>_outlier.png`、`<col>_distribution.png` |
| cols_encode | step1_cols_encode/ | sigmoid/sc/minmax/softmax/bins/count_encode_data.csv、z_score_model.pkl、min_max_model.pkl、bins_encode_detail.pkl、count_encode_detail.pkl、woe_encode_detail.pkl |
| get_bins.chi2_bin / bins_model | step2_bins_result/ | `<col>_chi2_bin_detail.xlsx`、bins_rpt_[adj_]{num,cat}_<bins_type>.xlsx、woe_mapping_<bins_type>.pkl、psi_single_rpt_<bins_type><base>_<cmp>.xlsx、psi_avg_rpt_<bins_type>.xlsx、`<col>_{bad_rate,woe_iv,lift,ks}_<bins_type>.png` |
| cols_select | step4_cols_select/ | null_pct_drop_detail.pkl、null_pct_select_cols.pkl、enu_drop_detail.pkl、enu_select_cols.pkl、iv_select_cols.pkl、iv_drop_detail.xlsx、psi_drop_detail.xlsx、psi_select_cols.pkl、corr_select_cols.pkl、corr_all_detail.xlsx、corr_matrix_selected.xlsx/.png、chi2_select_cols.pkl、chi2_df.xlsx、lg_RFE_model.pkl、lg_rfe_select_cols.pkl、lg_l1select_model.pkl、lg_l1_select_cols.pkl、ml_select_imp_data.xlsx、ml_select_cols.pkl、boot_select_imp_data.xlsx、boot_select_cols.pkl、vif_select_detail.xlsx、vif_select_cols.pkl |
| cols_select_js | step5_cols_js/ | `<method>_js.png`、js_recu_data_<method>.xlsx、js_cols_detail_<method>.pkl、js_select_cols_<method>.pkl |
| binary_model | step6_binary_model/（+ datas/ 子目录） | train.csv、test.csv、lr_coef.xlsx、lr_model.pkl、xgb_imp.xlsx、xgb_model.pkl、lgbm_imp.xlsx、lgbm_model.pkl、mlp_weight.csv、mlp_model.h5、svm_imp.xlsx、svm_model.pkl、rdf_imp.xlsx、rdf_model.pkl、`<type>_gs_adj.pkl`、`<type>_gs_adj_model_final.pkl`、`<type>_bs_adj.pkl`、`<type>_bs_adj_model_final.pkl`、confusion_matrix_<type>_<adj>.xlsx、xtest_pred.csv、xtrain_pred.csv、all_pred.csv、bins_rpt_<bins_type>_<type>_<adj>_<group>_bins.xlsx、`<type>_<group>bins_full_report.xlsx`、ks_curve/roc_curve/lift_curve/prob_distribution/score_distribution_*.png |
| uns_model | step6_uns_model/ | pca_cols.pkl、pca_data.csv、pca_model.pkl、pca_details.csv、pca_cols_corr.xlsx、SSE肘部法评估.png、轮廓系数评估.png、cluster_labels.pkl、kmeans_center.xlsx |
| linear_model | step6_linear_model/ | `<model_name>_model_base_estimate.xlsx`、`<model_name>_Significance_summary.xlsx`、`<model_name>_model.pkl`、ols_model_normal_distribution_estimate.xlsx、iv_ols1_model.pkl、iv_ols2_model.pkl |

（`<type>`=lr/xgb/lgbm/keras/svm/rdf；`<adj>`=noadj/gs_adj/bs_adj；`<method>`=lgb/xgb；`<model_name>`=ols/logit）

### 9.2 编码注意事项（写给 AI 的硬性规则）

1. **必须用子模块全路径导入**：本机 cbhpacks 的 `__init__.py` 是空文件，禁止 `from cbhpacks import *` / `from cbhpacks import xxx`（必然 ImportError）。一律 `from cbhpacks.模块名 import 符号`，模块归属见 0.1 节表格。
2. **签名以本文档为准**：源码中 `binary_model`、`cols_select_js` 的 docstring 示例、`mlp_fit` 默认值、`cols_select.lg_C` 默认值均已过时，按本文档写。
3. **位置参数顺序陷阱**：`desc_col(df, target, col, cols, ...)`、`linear_model(df, cols, iv_target, iv_col, target, ...)`、`cols_select_js(train, test, cols, target, ...)`、`binary_model(train, test, cols, target, ...)`。
4. **report 必填三参**：`mt.report(group, mth_col, base_mth)`，数据必须含月份列。
5. **cols_select 是链式的**：方法按调用顺序逐步缩减 `self.cols_s`；`iv_select` 依赖 `iv_data`、`psi_select` 依赖 `psi_data`、`corr_select` 依赖 `iv_data`，构造时务必传入。
6. **缺失值**：`chi2_select`/`logistic_select`/`vif_select` 内部用 `nan` 参数（默认 0）填充；`ml_select`/`boostrap_select` **不填充**，调用前自行 fillna；`uns_model` 必须先 fillna。
7. **bins_model 的 bins_type 合法值**：`eq_cnt`、`eq_distance`、`deci_tree_bin`、`chi2_bin`、`cat_bin`（`bins_rpt` 中的 `map_bin` 分支无对应实现，勿用；`map_bins_rpt` 有 bug，勿用）。
8. **to_hive 的 local_loc 文件名必须为 `to_hive.csv`**。
9. **pkl 用 joblib.load 读取**；csv/xlsx 用 pandas。
10. 所有 `path` 目录相对 `os.getcwd()` 自动创建；需要固定输出位置时先 `os.chdir()` 或传带前缀的相对路径。
11. 依赖库：pandas、numpy、scikit-learn、lightgbm、xgboost、keras、statsmodels、scikit-optimize(skopt)、paramiko、pymysql、pyhive、clickhouse-connect、jieba、matplotlib、seaborn、openpyxl、joblib、scipy、tqdm。
