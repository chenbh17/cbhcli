import os
import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import KFold, TimeSeriesSplit
from cbhpacks.bins_model import *
import matplotlib.pyplot as plt
pd.set_option('display.float_format', '{:.4f}'.format)
import warnings
warnings.filterwarnings("ignore")
from datetime import *
import jieba


class cols_operate:
    def __init__(self,df,mean_key,col,date_col,explode_method=",",col_split_method=",",date_type='%Y%m%d',jieba_method=","):
        self.df=df
        self.df_copy=df.copy()
        self.col=col
        self.mean_key=mean_key
        self.date_col=date_col
        self.explode_method=explode_method
        self.col_split_method=col_split_method
        self.date_type=date_type
        self.jieba_method=jieba_method

    def col_explode(self):
        print('===================================')
        print('将一行炸裂为多行，mean_key为主键，col为将被炸裂的列，默认分割符为逗号，若分割符为其他，可在参数explode_method中设置')
        data=self.df_copy.copy()
        explode_data=data.set_index(self.mean_key)[self.col].str.split(self.explode_method,expand=True).stack().reset_index(level=1,drop=True).to_frame(self.col).reset_index()
        print('===================================')
        return explode_data
    
    def col_to_T(self):
        print('===================================')
        print('将某一列col中每一行转置成每一列，并以单元格内容名称作为列名')
        data=self.df_copy.copy()
        data_T=data.T.rename(columns=data[self.col])
        data_T.drop(index=self.col,inplace=True)
        print('===================================')
        return data_T

    def col_to_cols(self):
        print('===================================')
        print('将某一列按照分隔符分成多列')
        data=self.df_copy.copy()
        multi_data=data.copy()
        multi_data[[self.col+str(i) for i in range(1, multi_data[self.col].apply(lambda x: len(x.split(self.col_split_method))).max()+1)]] = multi_data[self.col].str.split(self.col_split_method, expand=True)
        multi_data.drop(self.col,axis=1,inplace=True)
        print('===================================')
        return multi_data
    
    def date_col_trans(self):
        print('===================================')
        print('将日期格式转换，默认转换为YYYYMMDD，也可改变date_type指定转换格式，如date_type=%Y-%m-%d，时间格式将变为YYYY-MM-DD，类型为字符串')
        data=self.df_copy.copy()
        time_data=data.copy()
        time_data[self.date_col] = pd.to_datetime(time_data[self.date_col].astype(str))
        time_data[self.date_col]=time_data[self.date_col].apply(lambda x: x.strftime(self.date_type))
        print('===================================')
        return time_data

    def date_mth_year(self):
        print('===================================')
        print("将YYYYMMDD类型的时间转化为YYYYMM与YYYY,类型为str")
        data=self.df_copy.copy()
        data['mth']=data[self.date_col].astype(str).str[:6]
        data['year']=data[self.date_col].astype(str).str[:4]
        print('===================================')
        return data


    def jieba_trans(self):
        print('===================================')
        data=self.df_copy.copy()
        df=data.copy()
        for i,j in enumerate(df[self.col]):
            df[self.col][i]=self.jieba_method.join(jieba.cut(df[self.col][i]))
        print('===================================')
        return df





class desc_df:
    '''
    Paras
    df：输入数据集
    cols：数据集中的特征
    col：如想看单变量时输入，默认None
    cat_cols：默认[]，自动识别离散型与连续型特征；如想手动指定离散型特征list，则输入列名list即可

    用法
    desc=desc_df(df=df,cols=cols,cat_cols=['col9'])
    num_report, cat_report = desc.get_rpt()
    '''
    def __init__(self,df,cols,col=None,cat_cols=[],path='step0_desc_result'):
        self.df=df
        self.df_copy=df.copy()
        self.cols=cols
        self.cat_cols=cat_cols
        self.col=col
        self.col_copy=col
        self.path=path
        dir_name=path
        current_path = os.getcwd()
        path=os.path.join(os.getcwd(), dir_name)
        try:
            os.mkdir(path)  # 使用 os.makedirs(path, exist_ok=True) 可以避免多级目录的创建
            print(f"文件夹 '{dir_name}' 已成功创建在 '{current_path}'.")
        except FileExistsError:
            print(f"文件夹 '{dir_name}' 已存在于 '{current_path}'.")
        except PermissionError:
            print(f"无法创建文件夹于该路径 '{current_path}'.")

    
    def get_kind(self):
        return 'num' if self.df[self.col].dtype.kind in 'ifc' and self.df[self.col].nunique() > 10 else 'cat'
    
    def numeric_desc(self):
        col_df = {}
        col_df['col_name'] = self.df[self.col].name
        col_df['type'] = self.df[self.col].dtype
        col_df['n_unique'] = self.df[self.col].nunique()
        col_df['missing_rate'] = self.df[self.col].isnull().sum() / len(self.df[self.col])
        col_df['mean'] = self.df[self.col].mean()
        col_df['std'] = self.df[self.col].std()
        col_df['cv'] = self.df[self.col].std() / (abs(self.df[self.col].mean()) + 1e-10)
        col_df['skew'] = self.df[self.col].skew()
        col_df['median'] = self.df[self.col].median()
        col_df['25%'] = self.df[self.col].quantile(0.25)
        col_df['75%'] = self.df[self.col].quantile(0.75)
        col_df['min'] = self.df[self.col].min()
        col_df['max'] = self.df[self.col].max()
        return pd.DataFrame(col_df, index=[0])
    
    def categorical_desc(self):
        col_df = {}
        col_df['col_name'] = self.df[self.col].name
        col_df['value']=self.df[self.col].value_counts().keys()
        col_df['type'] = self.df[self.col].dtype
        col_df['n_unique'] = self.df[self.col].nunique()
        col_df['missing_rate'] = self.df[self.col].isnull().sum() / len(self.df[self.col])
        col_df['ratio'] = self.df[self.col].value_counts() / len(self.df[self.col])
        col_df = pd.DataFrame(col_df).reset_index().reset_index()
        return col_df[['col_name','value', 'type', 'n_unique', 'missing_rate', 'index', 'ratio']]
    
    def get_rpt(self):
        """
        return: num_report, cat_report
        """
        num_report, cat_report = pd.DataFrame(), pd.DataFrame()
        for col in self.cols:
            self.col=col
            if not self.cat_cols:
                kind = self.get_kind()
            else:
                kind = 'cat' if col in self.cat_cols else 'num'
            if kind == 'cat':
                cat_report = pd.concat([cat_report, self.categorical_desc()])
            else:
                num_report = pd.concat([num_report, self.numeric_desc()])
        num_report.reset_index(drop=True).to_excel(self.path+'/desc_num_rpt.xlsx')
        cat_report.reset_index(drop=True).to_excel(self.path+'/desc_cat_rpt.xlsx')
        self.col=self.col_copy
        return num_report.reset_index(drop=True), cat_report.reset_index(drop=True)



class desc_col(desc_df):
    '''
    Paras
    corr_threshold:pearson相关系数阈值，默认0.5

    用法
    dc=desc_col(df=df,cols=cols,target='target',col='col1',cat_cols=[],corr_threshold=0.5)
    dc.kind
    dc.relative_()
    dc.supervised_()
    dc.easy_od()
    dc.feat_card()
    一般仅使用dc.feat_card()即可输出全部结果并保存
    '''
    
    def __init__(self,df,target,col,cols,cat_cols=[],corr_threshold=0.5,path='step0_single_col_desc_result'):
        super().__init__(df=df,cols=cols,col=col,cat_cols=cat_cols,path=path)  # 调用父类的构造函数
        self.cols=cols
        self.target=target
        self.corr_threshold=corr_threshold
        self.path=path
        dir_name=path
        current_path = os.getcwd()
        path=os.path.join(os.getcwd(), dir_name)
        try:
            os.mkdir(path)  # 使用 os.makedirs(path, exist_ok=True) 可以避免多级目录的创建
            print(f"Directory '{dir_name}' created successfully at '{current_path}'.")
        except FileExistsError:
            print(f"Directory '{dir_name}' already exists at '{current_path}'.")
        except PermissionError:
            print(f"Permission denied: Unable to create directory at '{current_path}'.")
    
    def desc_(self):
        self.kind=desc_df.get_kind(self)
        print('===================================')
        print('[描述性分析]')
        if self.kind == 'num':
            print(desc_df.numeric_desc(self).T.rename(columns={0: ''}))
            n_tau = 10
            tau = [(i + 1) / n_tau for i in range(n_tau - 1)]
            q = self.df[self.col].quantile(tau).tolist()
            xlab = [f'{t} quantile: {v: .2f}' for t, v in list(zip(tau, q))]
            plt.figure()
            plt.barh(xlab, q)
            plt.savefig(self.path+'/'+self.col+'_quantile.png',bbox_inches='tight')
            plt.show()
            desc_df.numeric_desc(self).to_excel(self.path+'/'+self.col+'_desc.xlsx',index=None)
        else:
            print(desc_df.categorical_desc(self))
            desc_df.categorical_desc(self).to_excel(self.path+'/'+self.col+'_desc.xlsx',index=None)
    
    def relative_(self):
        print('===================================')
        print('[相关性分析]')
        sim_fea = [(k, v) for k, v in self.df[self.cols].corr('spearman')[self.col].to_dict().items() if k != self.col and abs(v) > self.corr_threshold]
        if len(sim_fea) == 0:
            print('无相关变量')
        else:
            print('特征\t相关系数')
            for k, v in sim_fea:
                print(f'{k}\t{v: .3f}')
            pd.DataFrame(sim_fea,columns=['cols_corred_with_'+self.col,'corr_value']).to_excel(self.path+'/'+self.col+'_corr.xlsx',index=None)
    
    
    def supervised_(self):#series
        self.kind=desc_df.get_kind(self)
        print('===================================')
        print('[有监督分析]')
        if self.kind=='num':
            bins_type='eq_cnt'
        else:
            bins_type='cat_bin'
        bm=bins_model(df=self.df,cols=None,group=10,target=self.target,nan=-9999,bins_type=bins_type,mth_col=None,base_mth=None,cmp_mth=None,col=self.col,adj_bin=False,cat_cols=None,path=self.path)
        woe,iv=bm.bins_rpt()
        print(f'IV:\t{iv :.3f}')
        woe.to_excel(self.path+'/'+self.col+'_woe.xlsx',index=None)
        plt.figure()
        plt.barh(woe['bucket'].astype(str),
                 woe['woe'])
        plt.title('WOE')
        plt.savefig(self.path+'/'+self.col+'_woe.png',bbox_inches='tight')
        plt.show()
    
    
    def easy_od(self, how='whisker'):#series
        print('===================================')
        print('[异常数据分析]')
        if how == 'whisker':
            q1, q3 = self.df[self.col].quantile([0.25, 0.75]).values
            delta = 1.5 * (q3 - q1)
            lower, upper = q1 - delta, q3 + delta
        elif how == '3sigma':
            lower, upper = self.df[self.col].mean() - 3 * self.df[self.col].std(), self.df[self.col].mean() + 3 * self.df[self.col].std()
        n1, n2 = (self.df[self.col] < lower).sum(), (self.df[self.col] > upper).sum()
    
        print(f'共有\t{n1}\t个样本小于下限区间')
        print(f'共有\t{n2}\t个样本大于上限区间')
        print(f'共计\t{n1 + n2}\t个离群点，占据总体{(n1 + n2) * 100 / len(self.df[self.col]) :.3f}%')
        print('===================================')
        return lower, upper, (n1 + n2) / len(self.df[self.col])
    
    
    def feat_card(self):
        self.kind=desc_df.get_kind(self)
        self.desc_()
        self.relative_()
        self.supervised_()
        if self.kind == 'num':
            self.easy_od()
            plt.figure(figsize=(10, 5))
            plt.subplot(1, 2, 1)
            plt.boxplot(self.df[self.col])
            plt.savefig(self.path+'/'+self.col+'_outlier.png',bbox_inches='tight')
            plt.subplot(1, 2, 2)
            plt.hist(self.df[self.col], bins=30)
            plt.savefig(self.path+'/'+self.col+'_distribution.png',bbox_inches='tight')
            plt.show()