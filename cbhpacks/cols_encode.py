import pandas as pd
import numpy as np
import warnings
import math
from sklearn.preprocessing import StandardScaler
from sklearn.preprocessing import MinMaxScaler
from datetime import *
from cbhpacks.bins_model import *
import joblib
warnings.filterwarnings("ignore")

class cols_encode:
    '''
    Paras
    df：输入数据集
    cols：输入特征列list
    bins_type：分箱类型
    group：分箱个数
    target：目标变量列名 string
    nan：空值赋值

    用法
    ce=cols_encode(df=df,cols=cols,bins_type='eq_cnt',group=10,target='target',nan=-9999)
    datas=ce.data_to_minmax()
    datas=ce.data_to_sc()
    datas=ce.data_to_sigmoid()
    datas=ce.data_to_softmax()
    datas,details=ce.bins_to_num()
    datas,woe_dic=ce.data_to_woe()
    datas,details=ce.str_to_num()
    '''
    def __init__(self,df,cols,sc_model=StandardScaler(),mm_model=MinMaxScaler(),bins_type='eq_cnt',group=10,target='target',nan=-9999,path='step1_cols_encode',adj_bin=False,min_group=2):
        self.df=df
        self.df_copy=df.copy()
        self.cols=cols
        self.bins_type=bins_type
        self.group=group
        self.target=target
        self.sc_model=sc_model
        self.mm_model=mm_model
        self.nan=nan
        self.bm=bins_model(df=df,cols=cols,group=group,target=target,nan=nan,bins_type=bins_type,mth_col=None,base_mth=None,cmp_mth=None,col=None,adj_bin=adj_bin,min_group=min_group,cat_cols=False,path=path)
        self.path=path
        dir_name=path
        current_path = os.getcwd()
        path=os.path.join(os.getcwd(), dir_name)
        try:
            os.makedirs(path, exist_ok=True)  # 使用 os.makedirs(path, exist_ok=True) 可以避免多级目录的创建
            print(f"文件夹 '{dir_name}' 已成功创建在 '{current_path}'.")
        except FileExistsError:
            print(f"文件夹 '{dir_name}' 已存在于 '{current_path}'.")
        except PermissionError:
            print(f"无法创建文件夹于该路径 '{current_path}'.")
        

    def data_to_sigmoid(self):
        data=self.df.copy()
        for i in self.cols:
            for j,x in enumerate(data[i]):
                data[i][j]=1/(1+math.exp(x*(-1)))
        data[self.cols].to_csv(self.path+'/sigmoid_encode_data.csv',index=None)
        return data
    
    def data_to_sc(self): 
        data=self.df.copy()
        sc = self.sc_model
        sc_data = sc.fit_transform(data[self.cols])
        for i,j in enumerate(self.cols):
            data[j]=pd.DataFrame(sc_data)[i]
        for i in self.cols:
            data[i]=round(data[i],4)
        data[self.cols].to_csv(self.path+'/sc_encode_data.csv',index=None)
        self.sc_model=sc
        joblib.dump(sc,self.path+'/z_score_model.pkl')
        return data
        
    def data_to_minmax(self):
        data=self.df.copy()
        mm = self.mm_model
        mm_data = mm.fit_transform(data[self.cols])
        for i,j in enumerate(self.cols):
            data[j]=pd.DataFrame(mm_data)[i]
        for i in self.cols:
            data[i]=round(data[i],4)
        data[self.cols].to_csv(self.path+'/minmax_encode_data.csv',index=None)
        joblib.dump(mm,self.path+'/min_max_model.pkl')
        return data
    
    def data_to_softmax(self):
        data=self.df.copy()
        for i in self.cols:
            values=data[i].values
             # 计算softmax值
            exp_values = np.exp(values - np.max(values))  # 避免指数溢出
            softmax_values = exp_values / np.sum(exp_values)
            data[i]=softmax_values
            data[i]=round(data[i],4)
        data[self.cols].to_csv(self.path+'/softmax_encode_data.csv',index=None)
        return data
    
    def bins_to_num(self):
        data=self.df.copy()
        details={}
        for i in self.cols:
            self.bm.col=i
            if self.bins_type=='eq_cnt':
                bin_edge,bins,bins_cnt=self.bm.eq_cnt()
            elif self.bins_type=='eq_distance':
                bin_edge,bins,bins_cnt=self.bm.eq_distance()
            elif self.bins_type=='deci_tree_bin':
                bin_edge,bins,bins_cnt=self.bm.deci_tree_bin()
            elif self.bins_type=='chi2_bin':
                bin_edge,bins,bins_cnt=self.bm.chi2_bin(initial_group=20)
            elif self.bins_type=='cat_bin':
                bin_edge,bins,bins_cnt=self.bm.cat_bin()
            details[i]={}
            for rk,bc in enumerate(bins_cnt.keys()):
                details[i][bc]=rk+1
            data[i]=bins
            data[i]=data[i].map(details[i])
        self.bm.col=None
        data[self.cols].to_csv(self.path+'/bins_encode_data.csv',index=None)
        joblib.dump(details,self.path+'/bins_encode_detail.pkl')
        return data,details
    
    def str_to_num(self):
        data=self.df.copy()
        details={}
        for i in self.cols:
            data[i]=data[i].fillna('')
            details[i]={}
            for num,key in enumerate(data[i].value_counts().keys()):
                details[i][key]=data[i].value_counts().reset_index().index[num]
            details[i][''] = self.nan
            str_dic_sort = dict(sorted(details[i].items(), key=lambda item: item[1]))
            for num,key in enumerate(str_dic_sort.keys()):
                if key == '':
                    details[i][key] = self.nan
                else:
                    details[i][key] = num+1
            try:
                details[i]['']=self.nan
            except:
                pass
            data[i]=data[i].map(details[i])
        data[self.cols].to_csv(self.path+'/count_encode_data.csv',index=None)
        joblib.dump(details,self.path+'/count_encode_detail.pkl')
        return data,details
    
    def data_to_woe(self):
        woe_df,woe_dic=self.bm.data_to_woe()
        joblib.dump(woe_dic,self.path+'/woe_encode_detail.pkl')
        return woe_df,woe_dic