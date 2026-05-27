import random
import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler

# 生成一个长度为num的随机数据表,包含月份、变量、y）
def get_random_data(min_edge,max_edge,num,mth_cnt):
    data = np.random.randint(min_edge, max_edge, num)
    data2 = np.random.randint(min_edge, max_edge, num)
    data3 = np.random.rand(num)
    data4=np.random.rand(num)
    data5=np.random.rand(num)
    data6=np.random.randint(min_edge, max_edge, num)
    data7=np.random.randint(min_edge, max_edge, num)
    data8=np.random.randint(1, 10, num)
    data9=np.random.randint(1, 10, num)
    mth_start=202401
    mth_end=mth_start+mth_cnt
    mth = np.random.randint(mth_start, mth_end, num)
    data=list(data)
    data2=list(data2)
    data_all=pd.DataFrame()
    data_all['mth']=mth
    data_all['col1']=data
    data_all['col2']=data2
    data_all['col3']=data3
    data_all['col4']=data4
    data_all['col5']=data5
    data_all['col6']=data6
    data_all['col7']=data7
    data_all['col8']=data8
    data_all['col9']=data9
    scaler = StandardScaler()
    scaled_features = scaler.fit_transform(data_all[['col1', 'col2','col3','col4','col5','col6','col7','col8','col9']])
    # 定义权重
    # weights = np.array([0.22, 0.22, 0.33, 0.33,0.22,0.22,0.22,0.22,0.22])#根据不同类型的变量数占比为权重线性加权
    weights = np.array([1, 1, 1, 1,1,1,1,1,1])#根据不同类型的变量数占比为权重线性加权
    # 计算线性组合
    linear_combination = scaled_features.dot(weights)
    # 定义 Sigmoid 函数
    def sigmoid(x):
        return 1 / (1 + np.exp(-x))
    # 计算概率
    probabilities = sigmoid(linear_combination)*0.9 #让小于0.5的概率更多，即目标变量为1的更少
    data_all['target'] = np.random.binomial(1, probabilities)
    data_all['id']=data_all.index
    def introduce_random_missing_values(df, cols, percent_missing):
        for i in cols:
            # 确定要加入缺失值的数量
            num_missing = int(len(df) * percent_missing)
            # 随机选择要加入缺失值的索引
            missing_indices = np.random.choice(df.index, num_missing, replace=False)
            # 在指定列中将这些索引对应的值设置为 NaN
            df.loc[missing_indices, i] = np.nan
        return df
    data_all=introduce_random_missing_values(data_all,['col4','col5','col6','col7','col8','col9'],0.2)
    data_all.id=data_all.id.astype(str)
    # 定义要选择的字母
    letters = ['A', 'B', 'C', 'D']
    # 定义一个函数来随机选择两个字母并拼接
    def random_letter_pair():
        return ','.join(random.sample(letters, 2))
    # 使用apply函数为DataFrame添加新列
    data_all['col10'] = data_all.apply(lambda x: random_letter_pair(), axis=1)
    data_all=data_all[['id','mth','target','col1','col2','col3','col4','col5','col6','col7','col8','col9','col10']]
    return data_all