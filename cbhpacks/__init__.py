"""
cbhpacks - 数据科学与机器学习工具包

包含以下模块:
- models_for_try: 测试数据生成
- bins_model: 分箱、WOE 转换、IV 计算
- model_training: 二分类模型、无监督学习、线性回归
- cols_select: 特征选择
- cols_encode: 特征编码
- preprocess: 数据预处理、描述统计
- con_linux: Linux 服务器连接
- con_sql: 数据库连接 (ClickHouse, MySQL, Hive)
"""

from cbhpacks.models_for_try import get_random_data
from cbhpacks.bins_model import get_bins, bins_model
from cbhpacks.model_training import binary_model, uns_model, linear_model
from cbhpacks.cols_select import cols_select, cols_select_js
from cbhpacks.cols_encode import cols_encode
from cbhpacks.preprocess import cols_operate, desc_df, desc_col
from cbhpacks.con_linux import con_linux, data_trans_linux, jps, hadoop, start_hive
from cbhpacks.con_sql import chrun, chdf, con_mysql, con_hive, get_create_table, to_hive, rfms_sql

__version__ = '1.0.0'
__all__ = [
    'get_random_data',
    'get_bins',
    'bins_model',
    'binary_model',
    'uns_model',
    'linear_model',
    'cols_select',
    'cols_select_js',
    'cols_encode',
    'cols_operate',
    'desc_df',
    'desc_col',
    'con_linux',
    'data_trans_linux',
    'jps',
    'hadoop',
    'start_hive',
    'chrun',
    'chdf',
    'con_mysql',
    'con_hive',
    'get_create_table',
    'to_hive',
    'rfms_sql'
]
