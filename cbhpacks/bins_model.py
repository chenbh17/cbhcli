import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings("ignore")
from sklearn.tree import DecisionTreeClassifier
from scipy.stats import chi2_contingency
import time
from datetime import datetime,timedelta
import matplotlib.pyplot as plt
import joblib
pd.set_option('display.float_format', '{:.4f}'.format)
import os


def chi_square_of_df_cols(data, col, target):
    #设定卡方检验
    contingency_table = pd.crosstab(data[col], data[target])
    chi2, p, _, _ = chi2_contingency(contingency_table)
    return chi2, p
    
class get_bins:
    def __init__(self,df,col,group,target,nan,path='step2_bins_result'):
        self.col=col
        self.group=group
        self.group_copy=group
        self.target=target
        self.nan=nan
        self.df=df
        self.path=path
        try:
            if self.df[col].min()<self.nan:
                print(f"!!!!!!!!!!!!!!!!!!!警告：变量{col}的最小值为'{str(self.df[col].min())}'，小于所赋缺失值{str(self.nan)}!!!!!!!!!!!!!!!!!!!")
        except:
            pass

        dir_name=path
        current_path = os.getcwd()
        path=os.path.join(os.getcwd(), dir_name)
        try:
            os.makedirs(path, exist_ok=True)  # 使用 os.makedirs(path, exist_ok=True) 可以避免多级目录的创建
            print(f"文件夹 '{dir_name}' 已成功创建在 '{current_path}'.")
        except:
            pass
    
    #等距分段（也称等分数、等宽分组）
    def eq_distance(self):
        data=self.df.copy()
        min_score=data[self.col].min(); max_score=data[self.col].max()
        if data[self.col].isnull().sum()!=0:
            data[self.col]=data[self.col].fillna(self.nan)
            if len(data[self.col].value_counts())<=self.group:
                step=(max_score - min_score)/len(data[self.col].value_counts())
                bin_edge=[min_score + i*step for i in range(len(data[self.col].value_counts())+1)]
            else:
                step=(max_score - min_score)/self.group
                bin_edge=[min_score + i*step for i in range(self.group+1)]
            # bin_edge.insert(0,self.nan-0.001)
            bin_edge.insert(0,-np.inf)#
            bin_edge[1]=min_score-0.001
            bin_edge[-1]=np.inf#
            bins=pd.cut(data[self.col],bin_edge)
            bins_cnt=bins.value_counts().sort_index()
            return bin_edge,bins,bins_cnt
        else:
            if len(data[self.col].value_counts())<=self.group:
                step=(max_score - min_score)/len(data[self.col].value_counts())
                bin_edge=[min_score + i*step for i in range(len(data[self.col].value_counts())+1)]
            else:
                step=(max_score - min_score)/self.group
                bin_edge=[min_score + i*step for i in range(self.group+1)]
            bin_edge[0]=-np.inf#
            bin_edge[-1]=np.inf#
            bins=pd.cut(data[self.col],bin_edge)
            bins_cnt=bins.value_counts().sort_index()
            return bin_edge,bins,bins_cnt
    #等频分段（也称等分位数分段）
    def eq_cnt(self):
        data=self.df.copy()
        if data[self.col].isnull().sum()!=0:
            if len(data[self.col].value_counts())<=self.group:
                # 生成百分位端点
                pct_bins = np.linspace(0, 100, len(data[self.col].value_counts()) + 1)
            else:
                # 生成百分位端点
                pct_bins = np.linspace(0, 100, self.group + 1)
            #根据百分位数计算分段边界
            bin_edge = list(np.percentile(data.loc[data[self.col].notnull()][self.col], pct_bins))
            bin_edge=list(set(bin_edge))
            bin_edge.sort()
            bin_edge[0]=self.nan
            bin_edge[-1]=np.inf
            bin_edge_null=[-np.inf,self.nan]#
            data[self.col]=data[self.col].fillna(self.nan)
            bins_null=pd.cut(data.loc[data[self.col]==self.nan][self.col],bin_edge_null)
            bins=pd.cut(data.loc[data[self.col]!=self.nan][self.col],bin_edge)
            bins_cnt=pd.DataFrame(bins.value_counts().sort_index()).reset_index()
            bins_cnt_null=pd.DataFrame(bins_null.value_counts().sort_index()).reset_index()
            vc=pd.concat([bins_cnt_null,bins_cnt]).reset_index(drop=True)
            bins_cnt=vc
            index_max=bins_cnt.index.max()
            l=[]
            for i,j in enumerate(bins_cnt[list(bins_cnt)[1]]):
                if j ==0 and i<index_max:
                    bins_cnt[list(bins_cnt)[0]][i+1]=pd.Interval(bins_cnt.loc[i][list(bins_cnt)[0]].left, bins_cnt.loc[i+1][list(bins_cnt)[0]].right, closed='right')
                    l.append(bin_edge[i])
                    bins_cnt.drop(index=i,inplace=True)
                elif j ==0 and i==index_max:
                    bins_cnt[list(bins_cnt)[0]][bins_cnt.index.max()-1]=pd.Interval(bins_cnt.iloc[-2][list(bins_cnt)[0]].left, bins_cnt.iloc[-1][list(bins_cnt)[0]].right, closed='right')
                    l.append(bin_edge[-2])
                    bins_cnt.reset_index(drop=True,inplace=True)
                    bins_cnt.drop(index=bins_cnt.index.max(),inplace=True)
                else:
                    pass
            for i in l:
                bin_edge.remove(i)
            bins_cnt.reset_index(drop=True,inplace=True)
            bins_cnt=bins_cnt.set_index(list(bins_cnt)[0],drop=True)[list(bins_cnt)[1]]
            bins_null=pd.cut(data.loc[data[self.col]==self.nan][self.col],bin_edge_null)
            bins=pd.cut(data.loc[data[self.col]!=self.nan][self.col],bin_edge)
            bins_merge = pd.Series(index=bins.index.union(bins_null.index)) 
            bins_merge[bins.index] = bins
            bins_merge[bins_null.index] = bins_null
            bins=bins_merge
            bin_edge.insert(0,-np.inf)
            return bin_edge,bins,bins_cnt
        else:
            if len(data[self.col].value_counts())<=self.group:
                # 生成百分位端点
                pct_bins = np.linspace(0, 100, len(data[self.col].value_counts()) + 1)
            else:
                # 生成百分位端点
                pct_bins = np.linspace(0, 100, self.group + 1)
            #根据百分位数计算分段边界
            bin_edge = list(np.percentile(data[self.col], pct_bins))
            bin_edge=list(set(bin_edge))
            bin_edge.sort()
            bin_edge[0]=-np.inf#
            bin_edge[-1]=np.inf#
            bins=pd.cut(data[self.col],bin_edge)
            bins_cnt=bins.value_counts().sort_index()
            return bin_edge,bins,bins_cnt
    # #决策树分段
    def deci_tree_bin(self,min_per=0.1):
        data=self.df.copy()
        bin_edge=[]
        if data[self.col].isnull().sum()!=0:
            if len(data[self.col].value_counts())<=self.group:
                deci_clf=DecisionTreeClassifier(criterion='entropy',max_leaf_nodes=len(data[self.col].value_counts()),min_samples_leaf=min_per)
            else:
                deci_clf=DecisionTreeClassifier(criterion='entropy',max_leaf_nodes=self.group,min_samples_leaf=min_per)
            deci_clf.fit(data.loc[data[self.col].notnull()][self.col].values.reshape(-1,1),data.loc[data[self.col].notnull()][self.target])
            n_nodes=deci_clf.tree_.node_count
            children_left=deci_clf.tree_.children_left
            children_right=deci_clf.tree_.children_right
            threshold=deci_clf.tree_.threshold
            for i in range(n_nodes):
                if children_left[i]!=children_right[i]:
                    bin_edge.append(threshold[i])
            bin_edge.sort()
            min_x=data[self.col].min()-0.001#  20250210
            max_x=np.inf#data[self.col].max()+0.001  20250210
            bin_edge=[min_x]+bin_edge+[max_x]
            data[self.col]=data[self.col].fillna(self.nan)
            bin_edge.insert(0,-np.inf)#
            #bin_edge.insert(1,self.nan)#
            bins=pd.cut(data[self.col],bin_edge)
            bins_cnt=bins.value_counts().sort_index()
            return bin_edge,bins,bins_cnt
        else:
            if len(data[self.col].value_counts())<=self.group:
                deci_clf=DecisionTreeClassifier(criterion='entropy',max_leaf_nodes=len(data[self.col].value_counts()),min_samples_leaf=min_per)
            else:
                deci_clf=DecisionTreeClassifier(criterion='entropy',max_leaf_nodes=self.group,min_samples_leaf=min_per)
            deci_clf.fit(data.loc[data[self.col].notnull()][self.col].values.reshape(-1,1),data.loc[data[self.col].notnull()][self.target])
            n_nodes=deci_clf.tree_.node_count
            children_left=deci_clf.tree_.children_left
            children_right=deci_clf.tree_.children_right
            threshold=deci_clf.tree_.threshold
            for i in range(n_nodes):
                if children_left[i]!=children_right[i]:
                    bin_edge.append(threshold[i])
            bin_edge.sort()
            #min_x=data[self.col].min()-0.001
            #max_x=data[self.col].max()+0.001
            min_x=-np.inf#
            max_x=np.inf#
            bin_edge=[min_x]+bin_edge+[max_x]
            bins=pd.cut(data[self.col],bin_edge)
            bins_cnt=bins.value_counts().sort_index()
            return bin_edge,bins,bins_cnt
    
    #卡方分段
    def chi2_bin(self, initial_group=20):
        # 步骤 1: 准备数据
        data = self.df.copy()
        if data[self.col].isnull().sum()>0:
            data[self.col]=data[self.col].fillna(self.nan)
            # 步骤 2: 计算初始分组
            self.group=initial_group
            initial_edge,bins,bins_cnt=self.eq_cnt()
            min_initial=initial_edge[:2]
            
            # 步骤 3: 初始化卡方信息列表
            chi_details = pd.DataFrame(columns=["Bin_Low", "Bin_High", "Chi_Square", "P_Value"])
            self.group=self.group_copy
            # 步骤 4: 开始分箱
            while len(initial_edge[2:]) > self.group:
                chi2_stats = []
                for i in range(len(initial_edge[2:]) - 1):
                    bin_low, bin_high = initial_edge[2:][i], initial_edge[2:][i + 1]
                    try:
                        data['temp_bin'] = pd.cut(data[self.col], [bin_low, bin_high])
                        chi2_stat, p_value = chi_square_of_df_cols(data=data.dropna(subset='temp_bin'), col=self.col, target=self.target)
                    except:
                        #print(f"变量{self.col}有空分箱")
                        chi2_stat,p_value=-9999,-9999
                    chi2_stats.append((bin_low, bin_high, chi2_stat, p_value))
                
                # 找到卡方统计量最小的相邻分组
                min_chi2_stat = min(chi2_stats, key=lambda x: x[2])
                min_bin_low, min_bin_high, min_chi2_stat, min_p_value = min_chi2_stat
                
                # 合并相邻分组
                initial_edge.remove(min_bin_low)
                initial_edge.remove(min_bin_high)
                initial_edge.append(min_bin_high)
                initial_edge.sort()
                # 记录卡方信息
                new_row_df = pd.DataFrame([{"Bin_Low": min_bin_low, "Bin_High": min_bin_high, "Chi_Square": min_chi2_stat, "P_Value": min_p_value}])
                chi_details = pd.concat([chi_details, new_row_df], ignore_index=True)
            
            # 步骤 5: 返回最终的分组边界和卡方信息
            try:
                data.drop('temp_bin',axis=1,inplace=True)
            except:
                pass
            bin_edge =min_initial+initial_edge
            bin_edge=list(set(bin_edge))
            bin_edge.sort()
            bin_edge[-1]=np.inf#
            bins=pd.cut(data[self.col],bin_edge)
            bins_cnt=bins.value_counts().sort_index()
            chi_details.to_excel(self.path+'/'+self.col+'_chi2_bin_detail.xlsx',index=None)
            return bin_edge, bins, bins_cnt
        else:
            # 步骤 2: 计算初始分组
            self.group=initial_group
            initial_edge,bins,bins_cnt=self.eq_cnt()
            min_initial=min(initial_edge)
        
            # 步骤 3: 初始化卡方信息列表
            chi_details = pd.DataFrame(columns=["Bin_Low", "Bin_High", "Chi_Square", "P_Value"])
            self.group=self.group_copy
            # 步骤 4: 开始分箱
            while len(initial_edge) > self.group:
                chi2_stats = []
                for i in range(len(initial_edge) - 1):
                    bin_low, bin_high = initial_edge[i], initial_edge[i + 1]
                    try:
                        data['temp_bin'] = pd.cut(data[self.col], [bin_low, bin_high])
                        chi2_stat, p_value = chi_square_of_df_cols(data=data.dropna(subset='temp_bin'), col=self.col, target=self.target)
                    except:
                        #print(f"变量{self.col}有空分箱")
                        chi2_stat,p_value=-9999,-9999
                    chi2_stats.append((bin_low, bin_high, chi2_stat, p_value))
                
                # 找到卡方统计量最小的相邻分组
                min_chi2_stat = min(chi2_stats, key=lambda x: x[2])
                min_bin_low, min_bin_high, min_chi2_stat, min_p_value = min_chi2_stat
                
                # 合并相邻分组
                initial_edge.remove(min_bin_low)
                initial_edge.remove(min_bin_high)
                initial_edge.append(min_bin_high)
                initial_edge.sort()
                # 记录卡方信息
                new_row_df = pd.DataFrame([{"Bin_Low": min_bin_low, "Bin_High": min_bin_high, "Chi_Square": min_chi2_stat, "P_Value": min_p_value}])
                chi_details = pd.concat([chi_details, new_row_df], ignore_index=True)
            # 步骤 5: 返回最终的分组边界和卡方信息
            try:
                data.drop('temp_bin',axis=1,inplace=True)
            except:
                pass
            bin_edge =[min_initial]+initial_edge
            bin_edge=list(set(bin_edge))
            bin_edge.sort()
            bin_edge[0]=-np.inf
            bin_edge[-1]=np.inf
            bins=pd.cut(data[self.col],bin_edge)
            bins_cnt=bins.value_counts().sort_index()
            chi_details.to_excel(self.path+'/'+self.col+'_chi2_bin_detail.xlsx',index=None)
            return bin_edge, bins, bins_cnt
    #离散型变量分段
    def cat_bin(self):
        data=self.df.copy()
        data[self.col]=data[self.col].fillna(self.nan)
        bin_edge=list(set(data[self.col].to_list()))
        bin_edge.sort()
        bins=data[self.col]
        bins_cnt=data[self.col].value_counts().sort_index()
        return bin_edge,bins,bins_cnt

class bins_model(get_bins):  # 继承自get_bins类
    '''
    Paras:
    df：输入数据集
    col: 如需单变量分析则输入col，可用于bins_rpt函数和plot_col_rpt函数
    cols：输入特证list
    cat_cols：当输入离散型特征时，cat_cols设置为True。连续型特征为False
    group，adj_bin：group为初始分组个数，adj_bin为是否调整分箱，为True时将按照坏客率、好客率、iv合并分箱，最小分箱为5
    target：二分类目标变量
    nan：数据集中要填充的空值
    bins_type：分箱类型，目前支持等频eq_cnt、等距eq_distance、决策树deci_tree_bin、卡方分箱chi2_bin
    mth_col, base_mth, cmp_mth：分别为月份列名string， 月份列中基准月份数据int/string（202401），和月份列中用于比较的月份数据int/string（202402）最好为int

    用法：
    bm=bins_model(df=df,cols=list(df)[2:-1],group=10,target='target',nan=-999,bins_type='eq_cnt',mth_col='mth',base_mth=202401,cmp_mth=202403,col='col7',adj_bin=True,cat_cols=False,path='step2_bins_result')
    bin_edge,bins,bins_cnt=bm.eq_cnt()
    woe,iv=bm.bins_rpt()
    woe_data,iv_data=bm.comp_woe_iv()
    woe_df,woe_detail=bm.data_to_woe()
    psi_data=bm.get_psi()
    psi_avg_data=bm.psi_mth_avg()
    bm.plot_col_rpt(show=False)
    bm.plot_cols_rpt(show=False)
    '''
    def __init__(self,df,cols,group,target,nan,bins_type,col=None,mth_col=None,base_mth=None,cmp_mth=None,chi2_initial_group=20,adj_bin=False,cat_cols=False,min_group=2,bad_rate_adj=0.01,good_rate_adj=0.01,iv_adj=0.01,map_bins_list=None,path='step2_bins_result'):
        super().__init__(df,col,group,target,nan,path)  # 调用父类的构造函数
        self.bins_type=bins_type
        self.cols=cols
        self.mth_col=mth_col
        self.base_mth=base_mth
        self.cmp_mth=cmp_mth
        self.df_copy=df.copy()
        self.col_copy=col
        self.adj_bin=adj_bin
        self.com_mth_copy=cmp_mth
        self.cat_cols=cat_cols
        self.path=path
        self.cols_bins_rpt=''
        self.cols_iv_data=''
        self.df=df
        self.min_group=min_group
        self.bad_rate_adj=bad_rate_adj
        self.good_rate_adj=good_rate_adj
        self.iv_adj=iv_adj
        self.chi2_initial_group=chi2_initial_group
        self.map_bins_list=map_bins_list
        try:
            if chi2_initial_group<group and self.bins_type=='chi2_bin':
                print("!!!!!!!!!!!!!!!!!!!警告：chi2_initial_group卡方初始分箱数小于group目标分箱数!!!!!!!!!!!!!!!!!!!")
        except:
            pass
        try:
            for i in self.cols:             
                if self.df[i].min()<self.nan and self.bins_type=='chi2_bin':
                    print(f"!!!!!!!!!!!!!!!!!!!警告：变量{i}的最小值为'{str(self.df[i].min())}'，小于所赋缺失值{str(self.nan)}!!!!!!!!!!!!!!!!!!!")
        except:
            pass
        
        dir_name=path
        current_path = os.getcwd()
        path=os.path.join(os.getcwd(), dir_name)
        try:
            os.makedirs(path, exist_ok=True)  # 使用 os.makedirs(path, exist_ok=True) 可以避免多级目录的创建
            print(f"文件夹 '{dir_name}' 已成功创建在 '{current_path}'.")
        except:
            pass

    def map_bins_rpt(self):
        if len(cols)!=len(self.map_bins_list):
            print("特征数与特征分箱list数不一致！")
            return None
        woe_data=pd.DataFrame()
        iv_data=pd.DataFrame()
        iv_value=[]
        var=[]
        for i,j in enumerate(self.cols):
            self.col=j
            data=self.df.copy()
            data[self.col]=data[self.col].fillna(self.nan)
            bin_edge=self.map_bins_list[i]
            bin_edge.sort()
            bins=pd.cut(data[self.col],bin_edge)
            bins_cnt=bins.value_counts().sort_index()
            woe_data=pd.DataFrame()
            woe_data[self.col]=data[self.col]
            woe_data[self.target]=data[self.target]
            woe_data['bucket']=bins
            total_cnt = len(data[self.target])  # 计算总样本数
            bad_total_cnt = data[self.target].sum()      # 计算坏样本数
            good_total_cnt =total_cnt-bad_total_cnt  # 计算好样本数
            bad_total_rate =bad_total_cnt/total_cnt
            good_total_rate =good_total_cnt/total_cnt
            woe_grp = woe_data.groupby('bucket',as_index=True)  # 按照分箱结果进行分组聚合
            woe_final = pd.DataFrame() 
            woe_final['col_name']=self.col
            woe_final['bad_cnt'] = woe_grp[self.target].sum()  # 每个箱体中坏样本的数量
            woe_final['good_cnt'] = woe_grp[self.target].count()-woe_final['bad_cnt']
            woe_final['total_cnt'] = woe_grp[self.target].count() # 每个箱体的总样本数
            woe_final['bad_rate'] = woe_final['bad_cnt']/woe_final['total_cnt'] # 每个箱体中坏样本所占总样本数的比例
            woe_final['good_rate'] = woe_final['good_cnt']/woe_final['total_cnt']  # 每个箱体中好样本所占总样本数的比例
            woe_final['badattr'] = woe_final['bad_cnt']/bad_total_cnt   # 每个箱体中坏样本所占坏样本总数的比例
            woe_final['goodattr'] = (woe_final['total_cnt'] - woe_final['bad_cnt'])/good_total_cnt  # 每个箱体中好样本所占好样本总数的比例
            woe_final['badattr_cum'] = woe_final['badattr'].cumsum()
            woe_final['goodattr_cum'] = woe_final['goodattr'].cumsum()
            woe_final['woe'] = np.log(woe_final['badattr']/woe_final['goodattr'])  # 计算每个箱体的woe值
            woe_final['iv_bin']=(woe_final['badattr']-woe_final['goodattr'])*woe_final['woe']
            iv = ((woe_final['badattr']-woe_final['goodattr'])*woe_final['woe']).sum()
            woe_final['ks_bin'] = np.abs(woe_final['badattr_cum'] - woe_final['goodattr_cum'])
            woe_final['lift']=woe_final['bad_rate']/bad_total_rate
            woe_final['iv']=iv
            woe_final['ks'] = woe_final['ks_bin'].max()
            woe_final['bins_type']=self.bins_type
            woe_final['col_name']=self.col
            woe_final.reset_index(inplace=True)
            woe_final=woe_final.replace(-np.inf,0)
            woe_final=woe_final.replace(np.inf,0)
            woe_data=pd.concat([woe_data,woe_final])
            var.append(j)
            iv_value.append(iv)
        iv_data['var']=var
        iv_data['iv_value']=iv_value
        self.col=self.col_copy
        self.cols_bins_rpt=woe_data
        self.cols_iv_data=iv_data   
        return woe_data,iv_data
    
    def bins_rpt(self):#bins_type分为eq_cnt,eq_distance,deci_tree_bin
        data=self.df.copy()
        if len(data[self.col].unique())==1 and self.cols is not None:
            self.cols.remove(self.col)
            print(self.col,'由于数据单一已从self.cols剔除')
            woe_final=pd.DataFrame()
            iv=0
            return woe_final,iv
        else:
            if self.bins_type=='eq_cnt':
                bin_edge,bins,bins_cnt=get_bins.eq_cnt(self)
            elif self.bins_type=='eq_distance':
                bin_edge,bins,bins_cnt=get_bins.eq_distance(self)
            elif self.bins_type=='deci_tree_bin':
                bin_edge,bins,bins_cnt=get_bins.deci_tree_bin(self)
            elif self.bins_type=='chi2_bin':
                bin_edge,bins,bins_cnt=get_bins.chi2_bin(self, initial_group=self.chi2_initial_group)
            elif self.bins_type=='cat_bin':
                bin_edge,bins,bins_cnt=get_bins.cat_bin(self)
            elif self.bins_type=='map_bin':
                bin_edge,bins,bins_cnt=get_bins.map_bin(self)
            woe_data=pd.DataFrame()
            woe_data[self.col]=data[self.col]
            woe_data[self.target]=data[self.target]
            woe_data['bucket']=bins
            total_cnt = len(data[self.target])  # 计算总样本数
            bad_total_cnt = data[self.target].sum()      # 计算坏样本数
            good_total_cnt =total_cnt-bad_total_cnt  # 计算好样本数
            bad_total_rate =bad_total_cnt/total_cnt
            good_total_rate =good_total_cnt/total_cnt
            woe_grp = woe_data.groupby('bucket',as_index=True)  # 按照分箱结果进行分组聚合
            woe_final = pd.DataFrame() 
            woe_final['col_name']=self.col
            woe_final['bad_cnt'] = woe_grp[self.target].sum()  # 每个箱体中坏样本的数量
            woe_final['good_cnt'] = woe_grp[self.target].count()-woe_final['bad_cnt']
            woe_final['total_cnt'] = woe_grp[self.target].count() # 每个箱体的总样本数
            woe_final['bad_rate'] = woe_final['bad_cnt']/woe_final['total_cnt'] # 每个箱体中坏样本所占总样本数的比例
            woe_final['good_rate'] = woe_final['good_cnt']/woe_final['total_cnt']  # 每个箱体中好样本所占总样本数的比例
            woe_final['badattr'] = woe_final['bad_cnt']/bad_total_cnt   # 每个箱体中坏样本所占坏样本总数的比例
            woe_final['goodattr'] = (woe_final['total_cnt'] - woe_final['bad_cnt'])/good_total_cnt  # 每个箱体中好样本所占好样本总数的比例
            woe_final['badattr_cum'] = woe_final['badattr'].cumsum()
            woe_final['goodattr_cum'] = woe_final['goodattr'].cumsum()
            woe_final['woe'] = np.log(woe_final['badattr']/woe_final['goodattr'])  # 计算每个箱体的woe值
            woe_final['iv_bin']=(woe_final['badattr']-woe_final['goodattr'])*woe_final['woe']
            iv = ((woe_final['badattr']-woe_final['goodattr'])*woe_final['woe']).sum()
            woe_final['ks_bin'] = np.abs(woe_final['badattr_cum'] - woe_final['goodattr_cum'])
            woe_final['lift']=woe_final['bad_rate']/bad_total_rate
            woe_final['iv']=iv
            woe_final['ks'] = woe_final['ks_bin'].max()
            woe_final['bins_type']=self.bins_type
            woe_final['col_name']=self.col
            woe_final.reset_index(inplace=True)
            if self.adj_bin==False:
                woe_final=woe_final.replace(-np.inf,0)
                woe_final=woe_final.replace(np.inf,0)
                return woe_final,iv
            else:
                for fitcnt in range(100):
                    merge_df=[]
                    if len(woe_final)<=1:
                        self.cols.remove(self.col)
                        print(f"变量{self.col}只分一箱体已被剔除")
                        return woe_final,iv
                    else:
                        pass
                    if self.df[woe_final.col_name[0]].isnull().sum()>0:#判断是否有空值分箱，如果有先加入空值分箱，后进行分箱合并，合并时不考虑空值分箱
                        i=1
                        merge_df.append(woe_final.iloc[0])
                    else:
                        i=0
                    while i <= len(woe_final)-2:
                        if woe_final.iloc[i].bad_rate<self.bad_rate_adj or woe_final.iloc[i].iv_bin<self.iv_adj or woe_final.iloc[i].good_rate<self.good_rate_adj or pd.isnull(woe_final.iloc[i].total_cnt) == True or woe_final.iloc[i].total_cnt==0:
                            bins_merge = woe_final.iloc[i]
                            bins_merge.bucket=pd.Interval(woe_final.iloc[i].bucket.left, woe_final.iloc[i+1].bucket.right, closed='right')
                            bins_merge.bad_cnt=woe_final.iloc[i].bad_cnt+woe_final.iloc[i+1].bad_cnt
                            bins_merge.good_cnt=woe_final.iloc[i].good_cnt+woe_final.iloc[i+1].good_cnt
                            bins_merge.total_cnt=woe_final.iloc[i].total_cnt+woe_final.iloc[i+1].total_cnt
                            bins_merge['bad_rate'] = bins_merge['bad_cnt']/bins_merge['total_cnt'] # 每个箱体中坏样本所占总样本数的比例
                            bins_merge['good_rate'] = bins_merge['good_cnt']/bins_merge['total_cnt']  # 每个箱体中好样本所占总样本数的比例
                            bins_merge['badattr'] = bins_merge['bad_cnt']/bad_total_cnt   # 每个箱体中坏样本所占坏样本总数的比例
                            bins_merge['goodattr'] = (bins_merge['total_cnt'] - bins_merge['bad_cnt'])/good_total_cnt  # 每个箱体中好样本所占好样本总数的比例
                            bins_merge['woe'] = np.log(bins_merge['badattr']/bins_merge['goodattr'])  # 计算每个箱体的woe值
                            bins_merge['iv_bin']=(bins_merge['badattr']-bins_merge['goodattr'])*bins_merge['woe']
                            bins_merge['lift']=bins_merge['bad_rate']/bad_total_rate
                            merge_df.append(bins_merge)
                            i+=2
                        else:
                            merge_df.append(woe_final.iloc[i])
                            i+=1
                            pass
                    if i < len(woe_final): #将最后一行分箱加入
                        if woe_final.iloc[-1].bad_rate<self.bad_rate_adj or woe_final.iloc[-1].iv_bin<self.iv_adj or woe_final.iloc[-1].good_rate<self.good_rate_adj or pd.isnull(woe_final.iloc[i].total_cnt) == True or woe_final.iloc[i].total_cnt==0:
                            bins_merge=woe_final.iloc[-1]
                            bins_merge.bucket=pd.Interval(merge_df[-1].bucket.left, woe_final.iloc[-1].bucket.right, closed='right')
                            bins_merge.bad_cnt=merge_df[-1].bad_cnt+woe_final.iloc[-1].bad_cnt
                            bins_merge.good_cnt=merge_df[-1].good_cnt+woe_final.iloc[-1].good_cnt
                            bins_merge.total_cnt=merge_df[-1].total_cnt+woe_final.iloc[-1].total_cnt
                            bins_merge['bad_rate'] = bins_merge['bad_cnt']/bins_merge['total_cnt'] # 每个箱体中坏样本所占总样本数的比例
                            bins_merge['good_rate'] = bins_merge['good_cnt']/bins_merge['total_cnt']  # 每个箱体中好样本所占总样本数的比例
                            bins_merge['badattr'] = bins_merge['bad_cnt']/bad_total_cnt   # 每个箱体中坏样本所占坏样本总数的比例
                            bins_merge['goodattr'] = (bins_merge['total_cnt'] - bins_merge['bad_cnt'])/good_total_cnt  # 每个箱体中好样本所占好样本总数的比例
                            bins_merge['woe'] = np.log(bins_merge['badattr']/bins_merge['goodattr'])  # 计算每个箱体的woe值
                            bins_merge['iv_bin']=(bins_merge['badattr']-bins_merge['goodattr'])*bins_merge['woe']
                            bins_merge['lift']=bins_merge['bad_rate']/bad_total_rate
                            merge_df[-1]=bins_merge
                        else:
                            merge_df.append(woe_final.iloc[i])
                    merge_df=pd.DataFrame(merge_df).reset_index(drop=True)
                    merge_df['badattr_cum'] = merge_df['badattr'].cumsum()
                    merge_df['goodattr_cum'] = merge_df['goodattr'].cumsum()
                    iv = ((merge_df['badattr']-merge_df['goodattr'])*merge_df['woe']).sum()
                    merge_df['iv']=iv
                    merge_df['ks'] = merge_df['ks_bin'].max()
                    merge_df['ks_bin'] = np.abs(merge_df['badattr_cum'] - merge_df['goodattr_cum'])
                    if len(merge_df)==len(woe_final) or len(merge_df)<=self.min_group: #当分箱不能再合并或分箱小于最小分箱时，结束合并迭代
                        break
                    else:
                        woe_final=merge_df#当不满足if条件时，继续进行合并迭代
                merge_df=merge_df.replace(-np.inf,0)
                merge_df=merge_df.replace(np.inf,0)
                return merge_df,iv
                
        
    def comp_woe_iv(self):
    #woe_data,iv_data=comp_woe_iv(data=data_all,cols=['col1','col2'],target='target',bins_type='deci_tree_bin',group=4)
        data=self.df.copy()
        start = datetime.now()
        woe_data=pd.DataFrame()
        iv_data=pd.DataFrame()
        iv_value=[]
        var=[]
        print("正在生成各变量分箱报告...")
        for i in self.cols[::-1]:
            self.col=i
            woe_final,iv=self.bins_rpt()
            woe_data=pd.concat([woe_data,woe_final])
            var.append(i)
            iv_value.append(iv)
        iv_data['var']=var
        iv_data['iv_value']=iv_value
        end = datetime.now()
        print(" 运行时间: ",round((end-start).seconds/60,2),'分钟')
        if self.cat_cols==True:
            if self.adj_bin==True:
                woe_data.to_excel(self.path+'/bins_rpt_adj_cat_'+self.bins_type+'.xlsx',index=None)
            else:
                woe_data.to_excel(self.path+'/bins_rpt_cat_'+self.bins_type+'.xlsx',index=None)
        else:
            if self.adj_bin==True:
                woe_data.to_excel(self.path+'/bins_rpt_adj_num_'+self.bins_type+'.xlsx',index=None)
            else:
                woe_data.to_excel(self.path+'/bins_rpt_num_'+self.bins_type+'.xlsx',index=None)
        self.col=self.col_copy
        self.cols_bins_rpt=woe_data
        self.cols_iv_data=iv_data
        return woe_data,iv_data
        
    def data_to_woe(self):
        data=self.df.copy()
        woe_mapping = {}
        if len(self.cols_bins_rpt)!=0:
            woe_data,iv_data=self.cols_bins_rpt,self.cols_iv_data
        else:
            print("正在生成各变量分箱报告...")
            woe_data,iv_data=self.comp_woe_iv()
        print("正在生成WOE转换字典...")
        for col, grp in woe_data.groupby('col_name'):
            woe_mapping[col] = {}
            for row in grp.itertuples(index=False):
                woe_mapping[col][row.bucket] = row.woe
        data[self.cols]=data[self.cols].fillna(self.nan)
        woedf=data
        print("正在将数据转换为WOE...")
        for i in self.cols:
            if i in woe_mapping:
                woedf[i] = woedf[i].map(woe_mapping[i])
        joblib.dump(woe_mapping,self.path+'/woe_mapping_'+self.bins_type+'.pkl')
        return woedf,woe_mapping

    def get_psi(self):
    #psi_data=get_psi(data=data_all,cols=['col1','col2'],mth_col='mth',base_mth=202302,cmp_mth=202303,bins_type='deci_tree_bin',group=6,target='target')
        data=self.df_copy
        data[self.mth_col]=data[self.mth_col].astype(int)
        data_mth_grp=data.groupby(self.mth_col)
        base_mth_data=data_mth_grp.get_group(self.base_mth)
        cmp_mth_data=data_mth_grp.get_group(self.cmp_mth)
        psi_data=pd.DataFrame()
        var_list=[]
        psi_list=[]
        base_grp_pct_list=[]
        cmp_grp_cnt_list=[]
        cmp_grp_pct_list=[]
        for i in self.cols:
            self.col=i
            self.df=base_mth_data
            if self.bins_type=='eq_cnt':
                base_bin_edge,base_grp_bins,base_grp_cnt=get_bins.eq_cnt(self)
            elif self.bins_type=='eq_distance':
                base_bin_edge,base_grp_bins,base_grp_cnt=get_bins.eq_distance(self)
            elif self.bins_type=='deci_tree_bin':
                base_bin_edge,base_grp_bins,base_grp_cnt=get_bins.deci_tree_bin(self)
            elif self.bins_type=='chi2_bin':
                base_bin_edge,base_grp_bins,base_grp_cnt=get_bins.chi2_bin(self)
            elif self.bins_type=='cat_bin':
                base_bin_edge,base_grp_bins,base_grp_cnt=get_bins.cat_bin(self)
                base_grp_cnt=pd.cut(base_mth_data[i],base_bin_edge).value_counts().sort_index()
            base_grp_pct=base_grp_cnt/(base_grp_cnt.sum())
            cmp_grp_cnt=pd.cut(cmp_mth_data[i],base_bin_edge).value_counts().sort_index()
            cmp_grp_pct=cmp_grp_cnt/(cmp_grp_cnt.sum())
            psi=((base_grp_pct-cmp_grp_pct)*np.log((base_grp_pct+0.00001)/(cmp_grp_pct+0.00001))).sum()
            psi_list.append(psi)
            var_list.append(i)
            base_grp_pct_list.append(base_grp_pct)
            cmp_grp_cnt_list.append(cmp_grp_cnt)
            cmp_grp_pct_list.append(cmp_grp_pct)
        psi_data['var']=var_list
        psi_data['psi']=psi_list
        psi_data['base_grp_pct']=base_grp_pct_list
        psi_data['cmp_grp_cnt']=cmp_grp_cnt_list
        psi_data['cmp_grp_pct']=cmp_grp_pct_list
        psi_data.to_excel(self.path+'/psi_single_rpt_'+self.bins_type+str(self.base_mth)+'_'+str(self.cmp_mth)+'.xlsx',index=None)
        self.col=self.col_copy
        self.df=self.df_copy
        return psi_data
        
    def psi_mth_avg(self):
    #psi_avg_data=psi_mth_avg(data=data_all,cols=['col1','col2'],target='target',mth_col='mth',base_mth=202301,bins_type='eq_cnt',group=4)
        data=self.df_copy
        start = datetime.now()
        cmp_mth_list=list(data[self.mth_col].unique())
        cmp_mth_list.remove(self.base_mth)
        psi_avg_data=pd.DataFrame()
        psi_mth_list=[]
        print("正在进行PSI均值计算...")
        for i in cmp_mth_list:
            self.cmp_mth=i
            psi_single_mth=self.get_psi()
            psi_list=list(psi_single_mth['psi'])
            psi_mth_list.append(psi_list)
        psi_mth_list=np.asarray(psi_mth_list)
        psi_avg=sum(psi_mth_list)/len(psi_mth_list)
        psi_avg_data['var']=self.cols
        psi_avg_data['psi']=psi_avg
        end = datetime.now()
        self.cmp_mth=self.com_mth_copy
        psi_avg_data.to_excel(self.path+'/psi_avg_rpt_'+self.bins_type+'.xlsx',index=None)
        print(" 运行时间: ",round((end-start).seconds/60,2),'分钟')
        return psi_avg_data

    def plot_col_rpt(self,show=False):
        rpt,iv_data=self.bins_rpt()
        plt.ioff()
        plt.figure(figsize=(8,20))
        plt.subplot(411)
        plt.title(rpt.col_name[0]+':  bad_rate', fontsize=20)
        plt.barh(rpt.bucket.astype(str), rpt.bad_rate, color='b', edgecolor='black')
        plt.xlabel('bad_rate',fontsize=16)
        plt.ylabel('bucket',fontsize=16)
        #添加文本框
        x_min, x_max = plt.xlim() # x 轴范围
        y_min, y_max = plt.ylim()  # y 轴范围
        plt.text(
            x_max,  # x 位置
            y_min,   # y 位置
            'bins_type:  '+str(rpt.bins_type[0]),  # 文本内容
            bbox=dict(facecolor='lightgrey', alpha=0.5, edgecolor='black'),  # 文本框样式
            fontsize=16,
            ha='right',  # 左对齐
            va='bottom'
        )
        plt.savefig(self.path+'/'+str(rpt.col_name[0])+'_bad_rate_'+self.bins_type+'.png', bbox_inches='tight')
        
        plt.figure(figsize=(8,20))
        plt.subplot(412)
        plt.title(rpt.col_name.unique()[0]+':woe', fontsize=20)
        plt.barh(rpt.bucket.astype(str), rpt.woe, color='b', edgecolor='black')
        plt.xlabel('woe',fontsize=16)
        plt.ylabel('bucket',fontsize=16)
        x_min, x_max = plt.xlim() # x 轴范围
        y_min, y_max = plt.ylim()  # y 轴范围
        plt.text(
            x_max,  # x 位置
            y_min,   # y 位置
            'bins_type:  '+str(rpt.bins_type[0])+'\niv:  '+str(round(rpt.iv[0],2)),  # 文本内容
            bbox=dict(facecolor='lightgrey', alpha=0.5, edgecolor='black'),  # 文本框样式
            fontsize=16,
            ha='right',  # 左对齐
            va='bottom'
        )
        plt.savefig(self.path+'/'+str(rpt.col_name[0])+'_woe_iv_'+self.bins_type+'.png', bbox_inches='tight')
        
        plt.figure(figsize=(8,20))
        plt.subplot(413)
        plt.title(rpt.col_name.unique()[0]+':lift', fontsize=20)
        plt.plot(rpt.bucket.index, rpt.lift, color='b')
        plt.xlabel('bucket',fontsize=16)
        plt.ylabel('lift',fontsize=16)
        x_min, x_max = plt.xlim() # x 轴范围
        y_min, y_max = plt.ylim()  # y 轴范围
        plt.text(
            x_max,  # x 位置
            y_min,   # y 位置
            'bins_type:  '+str(rpt.bins_type[0]),  # 文本内容
            bbox=dict(facecolor='lightgrey', alpha=0.5, edgecolor='black'),  # 文本框样式
            fontsize=16,
            ha='right',  # 左对齐
            va='bottom'
        )
        plt.savefig(self.path+'/'+str(rpt.col_name[0])+'_lift_'+self.bins_type+'.png', bbox_inches='tight')
        
        plt.figure(figsize=(8,20))
        plt.subplot(414)
        plt.title(rpt.col_name.unique()[0]+':ks', fontsize=20)
        plt.plot(rpt.bucket.index, rpt.goodattr_cum, color='b')
        plt.plot(rpt.bucket.index, rpt.badattr_cum, color='r')
        plt.xlabel('bucket',fontsize=16)
        plt.ylabel('attr_cum',fontsize=16)
        x_min, x_max = plt.xlim() # x 轴范围
        y_min, y_max = plt.ylim()  # y 轴范围
        plt.text(
            x_max,  # x 位置
            y_min,   # y 位置
            'bins_type:  '+str(rpt.bins_type[0])+'\nks:  '+str(round(rpt.ks[0],2)),  # 文本内容
            bbox=dict(facecolor='lightgrey', alpha=0.5, edgecolor='black'),  # 文本框样式
            fontsize=16,
            ha='right',  # 左对齐
            va='bottom'
        )
        plt.savefig(self.path+'/'+str(rpt.col_name[0])+'_ks_'+self.bins_type+'.png', bbox_inches='tight')
        if show==True:
            plt.show()
    
    def plot_cols_rpt(self,show=False):
        for i in self.cols:
            self.col=i
            self.plot_col_rpt(show=show)
        self.col=self.col_copy
