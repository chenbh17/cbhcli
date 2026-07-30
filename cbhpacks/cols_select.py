import pandas as pd
import numpy as np
import sklearn
import time
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from lightgbm import LGBMClassifier
import xgboost as xgb
from xgboost import XGBClassifier
from datetime import datetime,timedelta
from sklearn.feature_selection import SelectKBest
from sklearn.feature_selection import chi2
from sklearn.datasets import make_regression, make_classification
from sklearn.preprocessing import MinMaxScaler
from sklearn.feature_selection import RFE
from sklearn.feature_selection import SelectFromModel
from sklearn.metrics import roc_curve
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
import scipy.stats as stats
import warnings
warnings.filterwarnings("ignore")
import matplotlib.pyplot as plt
import seaborn as sbn
from sklearn.model_selection import GridSearchCV
from statsmodels.stats.outliers_influence import variance_inflation_factor
import joblib
import os

class cols_select:
    '''
    Paras
    df:输入数据集
    cols：输入特征列list
    target：目标变量str
    psi_data: bins_model跑出来的psi结果
    iv_data：bins_model跑出来的iv结果
    null_pct：单个变量缺失值占比筛选阈值，默认0.95以上的变量筛掉
    enu_cnt：单变量unique个数，默认某变量只有一种数据时删掉
    enu_pct：单变量枚举值个数占比，默认0.95以上的删掉
    psi_thres：psi阈值，默认大于0.1的删掉
    iv_thres：iv阈值，默认小于0.01的特征删掉
    corr_method：计算相关性方式，默认spearman，还可以设置pearson和kendall
    corr_thres：相关系数，默认0.8
    chi2_p_value_thres：卡方检验p值阈值，默认大于0.5删掉
    lg_method：逻辑回归筛选方式，默认递归筛选recursion，也可以使用l1正则化l1penalty
    lg_C：逻辑回归正则化倒数，值越小正则化越大，默认0.001
    ml_method：机器学习重复抽样运行，筛选掉每次重要性为0的特征，支持xgb、lgb、rdf，默认lgb
    boot_method：有放回抽样，筛掉运行n次后重要性均值小于阈值的特征，支持xgb、lgb、rdf，默认lgb
    boot_thres：重要性均值的阈值，默认小于100筛掉

    用法
    cs=cols_select(df=df,cols=cols,target='target',psi_data=psi_avg_data,iv_data=iv_data,null_pct=0.95,enu_cnt=10,enu_pct=0.95,psi_thres=2,iv_thres=0.1
               ,corr_method='spearman',corr_thres=0.8,chi2_p_value_thres=0.5,lg_method='recursion',lg_C=0.001,ml_method='xgb',boot_method='xgb',boot_thres=0.2)
    null_cols=cs.null_select()
    enumerate_cols=cs.enumerate_select()
    iv_cols=cs.iv_select()
    psi_cols=cs.psi_select()
    corr_stay,corr_data,corr_matrix=cs.corr_select()
    cols_chi2,chi2_df=cs.chi2_select()
    lg_cols=cs.logistic_select()
    ml_cols,ml_imp=cs.ml_select()
    boot_cols,boot_imp_data=cs.boostrap_select()
    vif_cols,vif_detail=cs.vif_select()
    '''

    def __init__(self,df,cols,target,psi_data=None,iv_data=None
                 ,null_pct=0.95,enu_cnt=1,enu_pct=0.95
                 ,psi_thres=0.1,iv_thres=0.01,corr_method='spearman',corr_thres=0.8
                 ,chi2_p_value_thres=0.5,lg_method='recursion',lg_C=0.1
                 ,ml_method='lgb',boot_method='lgb',boot_thres=100,vif_thres=10,nan=0,path='step4_cols_select'):
        self.df=df
        self.cols=cols
        self.cols_s=cols.copy()
        self.df_copy=df.copy()
        self.target=target
        self.psi_data=psi_data
        self.iv_data=iv_data
        self.null_pct=null_pct
        self.enu_cnt=enu_cnt
        self.enu_pct=enu_pct
        self.psi_thres=psi_thres
        self.iv_thres=iv_thres
        self.corr_method=corr_method
        self.corr_thres=corr_thres
        self.chi2_p_value_thres=chi2_p_value_thres
        self.target=target
        self.lg_method=lg_method
        self.lg_C=lg_C
        self.ml_method=ml_method
        self.boot_method=boot_method
        self.boot_thres=boot_thres
        self.vif_thres=vif_thres
        self.nan=nan

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
        # try:
        #     os.makedirs(path, exist_ok=True)  # 使用 os.makedirs(path, exist_ok=True) 可以避免多级目录的创建
        #     print(f"Directory '{dir_name}' created successfully at '{current_path}'.")
        # except FileExistsError:
        #     print(f"Directory '{dir_name}' already exists at '{current_path}'.")
        # except PermissionError:
        #     print(f"Permission denied: Unable to create directory at '{current_path}'.")
            
    #缺失值筛选变量        
    def null_select(self):
        print("---------------------------------------------")
        data=self.df[self.cols_s].copy()
        null_detail={}
        print('正在进行缺失值筛选...')
        print('待筛选特征数',len(self.cols_s))
        start = datetime.now()
        for i in self.cols_s[::-1]:
            if (data[i].isnull().sum()/len(data[i])>self.null_pct):
                null_detail[i]=data[i].isnull().sum()/len(data[i])
                self.cols_s.remove(i)
        print('最终剩余变量数:',len(self.cols_s))
        end = datetime.now()
        print(" 运行时间: ",round((end-start).seconds/60,2),'分钟')
        print("---------------------------------------------")
        joblib.dump(null_detail,self.path+'/null_pct_drop_detail.pkl')
        joblib.dump(self.cols_s,self.path+'/null_pct_select_cols.pkl')
        self.null_cols=self.cols_s.copy()
        return self.cols_s


    #枚举值筛选变量
    def enumerate_select(self):#pct是某个特征中某个数据占比的阈值(大于它会被筛除），cnt是某个特征的数据枚举个数阈值（小于它会被筛除）
        data=self.df[self.cols_s].copy()
        print("---------------------------------------------")
        print('正在进行枚举值筛选...')
        print('待筛选特征数',len(self.cols_s))
        enu_detail={}
        start = datetime.now()
        for x in self.cols_s[::-1]:
            if (data[x].value_counts().iloc[0]/len(data)>=self.enu_pct) | (len(data[x].value_counts())<=self.enu_cnt):
                enu_detail[x]=(data[x].value_counts().iloc[0]/len(data),(len(data[x].value_counts())))
                self.cols_s.remove(x)
        print('最终剩余变量数:',len(self.cols_s))
        joblib.dump(enu_detail,self.path+'/enu_drop_detail.pkl')
        joblib.dump(self.cols_s,self.path+'/enu_select_cols.pkl')
        end = datetime.now()
        print(" 运行时间: ",round((end-start).seconds/60,2),'分钟')
        print("---------------------------------------------")
        self.enumerate_cols=self.cols_s.copy()
        return self.cols_s


    #iv筛选变量
    def iv_select(self):
        print("---------------------------------------------")
        print('正在进行iv筛选...')
        print('iv参考阈值： <0.03:无预测能力',' 0.03-0.09:低预测能力',' 0.1-0.29:中预测能力',' 0.3-0.49:强预测能力',' >0.5:极强预测能力')
        self.iv_data=self.iv_data[self.iv_data['var'].isin(self.cols_s)]
        cols_drop=list(self.iv_data[self.iv_data['iv_value']<=self.iv_thres]['var'])
        drop_data=self.iv_data[self.iv_data['iv_value']<=self.iv_thres]
        for i in cols_drop:
            self.cols_s.remove(i)
        joblib.dump(self.cols_s,self.path+'/iv_select_cols.pkl')
        drop_data.to_excel(self.path+'/iv_drop_detail.xlsx',index=None)
        print('最终剩余变量数:',len(self.cols_s))
        print("---------------------------------------------")
        self.iv_cols=self.cols_s.copy()
        return self.cols_s

    #psi筛选变量
    def psi_select(self):
        print("---------------------------------------------")
        print('正在进行psi筛选...')
        self.psi_data=self.psi_data[self.psi_data['var'].isin(self.cols_s)]
        self.cols_s=list(self.psi_data[self.psi_data['psi']<self.psi_thres]['var'])
        self.psi_data[~self.psi_data['var'].isin(self.cols_s)].to_excel(self.path+'/psi_drop_detail.xlsx',index=None)
        joblib.dump(self.cols_s,self.path+'/psi_select_cols.pkl')
        print('最终剩余变量数:',len(self.cols_s))
        print("---------------------------------------------")
        self.psi_cols=self.cols_s.copy()
        return self.cols_s



    #相关性筛选变量
    def corr_select(self):#method:kendall,spearman,pearson
        print("---------------------------------------------")
        print('相关性筛选，待筛选特征数',len(self.cols_s))
        data=self.df[self.cols_s].copy()
        start= datetime.now()
        corr_data=pd.DataFrame()
        var1=[]
        var2=[]
        corr_value=[]
        for i,j in enumerate(self.cols_s):
            for x in self.cols_s[i+1:]:
                var1.append(j)
                var2.append(x)
                corr_value.append(data[[j,x]].corr(method=self.corr_method).iloc[0][1])
        corr_data['var1']=var1
        corr_data['var2']=var2
        corr_data['corr_value']=corr_value
        corr_data['z_score']=corr_data['corr_value'] * ( (len(data)-2) ** 0.5 ) / ( (1 - corr_data['corr_value'] ** 2) ** 0.5 )
        corr_data['p_value']=2 * (1 - stats.norm.cdf(abs(corr_data['z_score'])))
        corr_data=pd.merge(left=pd.merge(left=corr_data,right=self.iv_data,how='left',left_on='var1',right_on='var'),right=self.iv_data,how='left',left_on='var2',right_on='var')
        corr_data['iv_diff']=corr_data['iv_value_x']-corr_data['iv_value_y']
        corr_data=corr_data[['var1','var2','corr_value','z_score','p_value','iv_diff']]
        corr_stay=self.cols_s
        corr_drop=[]
        for i,j in enumerate(corr_data.var1.to_list()):
            if abs(corr_data['corr_value'][i])>=self.corr_thres: 
                if corr_data.iv_diff[i]<0:
                    try:
                        corr_stay.remove(j)
                        corr_drop.append(j)
                    except:
                        pass
        c=corr_data[~corr_data['var1'].isin(corr_drop)].reset_index(drop=True)
        for i,j in enumerate(c.var2.to_list()):
            if abs(c['corr_value'][i])>=self.corr_thres: 
                if c.iv_diff[i]>=0:
                    try:
                        corr_stay.remove(j)
                    except:
                        pass
        print('最终剩余变量数:',len(corr_stay))
        #输出相关性矩阵
        corr_matrix = data[corr_stay].corr(method=self.corr_method)
        # 绘制热力图
        plt.figure(figsize=(12, 10))
        sbn.heatmap(corr_matrix, annot=True, cmap='coolwarm', fmt=".2f", linewidths=.5)
        plt.title('Correlation Heatmap')
        plt.show()
        end = datetime.now()
        print(" 运行时间: ",round((end-start).seconds/60,2),'分钟')
        print("---------------------------------------------")
        self.cols_s=corr_stay
        joblib.dump(self.cols_s,self.path+'/corr_select_cols.pkl')
        corr_data.to_excel(self.path+'/corr_all_detail.xlsx',index=None)
        corr_matrix.to_excel(self.path+'/corr_matrix_selected.xlsx',index=None)
        plt.savefig(self.path+'/corr_matrix_selected.png',bbox_inches='tight')
        self.corr_cols=self.cols_s.copy()
        return corr_stay,corr_data,corr_matrix



    #卡方检验筛选变量
    def chi2_select(self):
        data=self.df[self.cols_s+[self.target]].fillna(self.nan).copy()
        print("---------------------------------------------")
        print('正在进行卡方筛选...')
        print('待筛选特征数',len(self.cols_s))
        start= datetime.now()
        minmax_x = MinMaxScaler().fit_transform(data[self.cols_s])#将数据以最大值最小值之差为分母的方式转换到01之间
        chi2_value, p_value = chi2(minmax_x, data[self.target])#计算每个特征的卡方统计量和显著性p值
        chi2_df = pd.DataFrame(data=[self.cols_s, chi2_value, p_value]).T
        chi2_df.columns = ['var', 'chi2_value', 'p_value']
        chi2_df.sort_values(by='chi2_value', ascending=False, inplace=True)
        cols_chi2=chi2_df[chi2_df['p_value']<self.chi2_p_value_thres]['var'].to_list()
        print('最终剩余变量数:',len(cols_chi2))
        self.cols_s=cols_chi2
        end = datetime.now()
        print(" 运行时间: ",round((end-start).seconds/60,2),'分钟')
        joblib.dump(self.cols_s,self.path+'/chi2_select_cols.pkl')
        chi2_df.to_excel(self.path+'/chi2_df.xlsx',index=None)
        print("---------------------------------------------")
        self.chi2_cols=cols_chi2
        return cols_chi2,chi2_df


    #逻辑回归筛选变量
    def logistic_select(self):#mehtod='recursion'或'l1penalty'
        print("---------------------------------------------")
        data=self.df[self.cols_s+[self.target]].fillna(self.nan).copy()
        print('提示：筛选方式分为递归筛选recursion与l1惩罚项筛选l1penalty,C越小正则化越强，默认为0.1')
        print('剩余特征少时用递归筛选，剩余特征多时用l1正则筛选')
        print('待筛选特征数',len(self.cols_s))
        start = datetime.now()
        if self.lg_method=='recursion':
            print('正在进行逻辑回归递归筛选...')
            rfe=RFE(estimator=LogisticRegression(n_jobs=-1,solver='saga'))
            x_new=rfe.fit_transform(data[self.cols_s],data[self.target])
            selected_cols=data[self.cols_s].columns[rfe.get_support()].to_list()
            print('最终剩余变量数:',len(selected_cols))
            end = datetime.now()
            self.cols_s=selected_cols
            print('Selected features:',self.cols_s)
            print('Feature ranking:',rfe.ranking_)
            print('Feature count:',rfe.n_features_)
            print('Estimator_:',rfe.estimator_)
            print('Params:',rfe.get_params())
            joblib.dump(rfe,self.path+'/lg_RFE_model.pkl')
            joblib.dump(self.cols_s,self.path+'/lg_rfe_select_cols.pkl')
            print(" 运行时间: ",round((end-start).seconds/60,2),'分钟')
            print("---------------------------------------------")
            self.lr_cols=selected_cols
            return selected_cols
        elif self.lg_method=='l1penalty':
            print('正在进行逻辑回归l1正则化筛选...')
            LR=LogisticRegression(penalty='l1',C=self.lg_C,solver='saga',n_jobs=-1)
            sf=SelectFromModel(LR)
            x_new=sf.fit_transform(data[self.cols_s],data[self.target])
            selected_cols=data[self.cols_s].columns[sf.get_support()].to_list()
            self.cols_s=selected_cols
            print('最终剩余变量数:',len(selected_cols))
            end = datetime.now()
            print('Selected features:',self.cols_s)
            print('Estimator_:',sf.estimator_)
            print('Params:',sf.get_params())
            joblib.dump(sf,self.path+'/lg_l1select_model.pkl')
            joblib.dump(self.cols_s,self.path+'/lg_l1_select_cols.pkl')
            print(" 运行时间: ",round((end-start).seconds/60,2),'分钟')
            print("---------------------------------------------")
            self.lr_cols=selected_cols
            return selected_cols
            
    #lgb/xgb/随机森林特征重要性筛选
    def ml_select(self):
        data=self.df[self.cols_s+[self.target]].copy()
        print("---------------------------------------------")
        print('机器学习方法method分为随机森林rdf、Xgboost xgb、Lightgbm lgb，默认为lgb。用于筛选重要性为0的变量')
        start = datetime.now()
        for i in range(100):
            if self.ml_method=='rdf':
                print('正在进行随机森林筛选...')
                clf=RandomForestClassifier(n_jobs=-1,random_state=666)
            elif self.ml_method=='xgb':
                print('正在进行xgb筛选...')
                clf=XGBClassifier(importance_type='gain',n_jobs=-1,random_state=666)
            elif self.ml_method=='lgb':
                print('正在进行lgb筛选...')
                clf=LGBMClassifier(importance_type='gain',n_jobs=-1,verbose=-1,random_state=666)
            clf.fit(data[self.cols_s],data[self.target])
            imp_data=pd.DataFrame()
            imp_data['var']=self.cols_s
            imp_data['importances']=clf.feature_importances_
            self.cols_s=imp_data[imp_data['importances']!=0]['var'].to_list()
            cols_drop=imp_data[imp_data['importances']==0]['var'].to_list()
            print('已运行',i+1,'次迭代')
            if len(cols_drop)==0:
                print('最终剩余变量数:',len(self.cols_s))
                imp_data.to_excel(self.path+'/ml_select_imp_data.xlsx',index=None)
                joblib.dump(self.cols_s,self.path+'/ml_select_cols.pkl')
                end = datetime.now()
                print(" 运行时间: ",round((end-start).seconds/60,2),'分钟')
                print("---------------------------------------------")
                self.ml_cols=self.cols_s.copy()
                return self.cols_s,imp_data



    #boostrap筛选变量
    def boostrap_select(self, num_iterations=10,frac=1):
        """
        参数：
        data: pandas DataFrame，包含特征和目标列
        cols: list，待筛选特征的列表
        num_iterations: int，Bootstrap迭代次数，默认10次
        frac,随机抽样行数占总行数比例，默认每次总体全抽样
        method:lgb
        返回：
        selected_features: list，筛选后的特征列表
        """
        print("---------------------------------------------")
        print('正在进行boostrap筛选...')
        print('默认用lgb筛选，全抽样迭代10次，用于筛选重要性较低的变量，默认取重要性大于1000的变量。如需用xgb或随机森林rdf则需减少抽样比例frac与迭代次数num_iterations以保证效率')
        start = datetime.now()
        data=self.df[self.cols_s+[self.target]].copy()
        imp_data=pd.DataFrame()
        imp_data['var']=self.cols_s
        for i in range(num_iterations):
            # 从原始数据表中进行有放回的随机采样，得到新的数据表
            bootstrap_sample = data.sample(frac=frac, replace=True)
            if self.boot_method=='lgb':
                # 使用随机森林或其他模型进行训练
                clf = LGBMClassifier(importance_type='gain',n_jobs=-1,verbose=-1,random_state=666)
            elif self.boot_method=='xgb':
                clf = XGBClassifier(importance_type='gain',n_jobs=-1,random_state=666)
            elif self.boot_method=='rdf':
                clf = RandomForestClassifier(n_jobs=-1)
            clf.fit(bootstrap_sample[self.cols_s], bootstrap_sample[self.target])
            # 累加每次迭代中特征重要性
            imp_data['importances_'+str(i+1)]=clf.feature_importances_
            # 计算特征重要性的平均值
        imp_cols=list(imp_data.columns[1:])
        imp_data['imp_avg']=imp_data[imp_cols].sum(axis=1)/len(imp_cols)
        boos_cols=imp_data[imp_data['imp_avg']>self.boot_thres].reset_index(drop=True)['var'].to_list()
        print('最终剩余变量数:',len(boos_cols))
        self.cols_s=boos_cols
        imp_data.to_excel(self.path+'/boot_select_imp_data.xlsx',index=None)
        joblib.dump(self.cols_s,self.path+'/boot_select_cols.pkl')
        end = datetime.now()
        print(" 运行时间: ",round((end-start).seconds/60,2),'分钟')
        print("---------------------------------------------")
        self.boos_cols=boos_cols
        return boos_cols,imp_data


    def vif_select(self) :
        # 复制数据以避免更改原始数据
        print("---------------------------------------------")
        print("待筛选特征数："+str(len(self.cols_s)))
        df_filtered = self.df[self.cols_s+[self.target]].fillna(self.nan).copy()
        cols_copy=self.cols_s.copy()
        # 初始化详细信息 DataFrame
        filter_details = pd.DataFrame(columns=['Step', 'Feature', 'VIF'])
        X = df_filtered[cols_copy]
        # 计算初始 VIF
        vif = pd.DataFrame()
        vif['Feature'] = X.columns
        vif['VIF'] = [variance_inflation_factor(X.values, i) for i in range(X.shape[1])]
        # 筛选过程
        step = 0
        while vif['VIF'].max() > self.vif_thres:
            # 记录当前的 VIF 值
            current_details = pd.DataFrame({
                'Step': [step] * len(vif),
                'Feature': vif['Feature'],
                'VIF': vif['VIF']
            })
            filter_details = pd.concat([filter_details, current_details], ignore_index=True)
            # 找到 VIF 最大的特征
            max_vif_feature = vif[vif['Feature']!='const'].sort_values('VIF', ascending=False).iloc[0]['Feature']
            # 从特征集中移除该特征
            cols_copy.remove(max_vif_feature)
            X = df_filtered[cols_copy]
            # 重新计算 VIF
            vif = pd.DataFrame()
            vif['Feature'] = X.columns
            vif['VIF'] = [variance_inflation_factor(X.values, i) for i in range(X.shape[1])]
            step += 1
        current_details = pd.DataFrame({
            'Step': [step] * len(vif),
            'Feature': vif['Feature'],
            'VIF': vif['VIF']
        })
        filter_details = pd.concat([filter_details, current_details], ignore_index=True)
        self.cols_s=cols_copy
        filter_details.to_excel(self.path+'/vif_select_detail.xlsx',index=None)
        joblib.dump(cols_copy,self.path+'/vif_select_cols.pkl')
        print("剩余特征数："+str(len(self.cols_s)))
        print("---------------------------------------------")
        #返回筛选后的特征列表和筛选详细信息
        self.vif_cols=cols_copy
        return cols_copy, filter_details

    


class cols_select_js:
    '''
    Paras
    method：支持xgb与lgb，默认xgb
    shuffle：数据集划分是否排序，True为打乱划分，False为顺序划分
    random_state：随机数因子，默认666
    test_size：测试集数据量占比，默认0.2
    recursion_num：递归迭代次数，默认30次
    stay_pct：每次迭代，除筛掉重要性为0的特征外，其他变量的保留比例。默认0.95
    
    用法
    js=cols_select_js(df,cols,target='target',method='xgb',shuffle=True,random_state=666,test_size=0.2,recursion_num=30,stay_pct=0.95,path='step5_cols_js')
    js_data,cols_detail,js_cols=js.recursion_select()
    '''
    
    def __init__(self,train,test,cols,target,method='lgb',recursion_num=30,stay_pct=0.95,path='step5_cols_js'):
        self.xtrain=train
        self.xtest=test
        self.cols=cols
        self.target=target
        self.method=method
        self.recursion_num=recursion_num
        self.stay_pct=stay_pct
        self.cols_copy=cols.copy()

        self.path=path
        dir_name=path
        current_path = os.getcwd()
        path=os.path.join(os.getcwd(), dir_name)
        try:
            os.makedirs(path, exist_ok=True)  # 使用 os.makedirs(path, exist_ok=True) 可以避免多级目录的创建
            print(f"Directory '{dir_name}' created successfully at '{current_path}'.")
        except FileExistsError:
            print(f"Directory '{dir_name}' already exists at '{current_path}'.")
        except PermissionError:
            print(f"Permission denied: Unable to create directory at '{current_path}'.")
            
    def recursion_select(self,*args, **kwargs):
        print("---------------------------------------------")
        print('迭代筛选，每次筛掉gain为0与gain排名后5%(stay_pct=0.95)的变量，默认迭代30次(num=30)，返回每次迭代对应评估结果和迭代入模变量')
        start_total = datetime.now()
        js_data=pd.DataFrame()
        stay_colnum=[]
        acc_data=[]
        ks_data=[]
        auc_data=[]
        accdata_train=[]
        ksdata_train=[]
        aucdata_train=[]
        run_cnt=[]
        cols_detail=[]
        for i in range(self.recursion_num):
            start = datetime.now()
            stay_cnt=len(self.cols)
            stay_colnum.append(stay_cnt)
            cols_detail.append(self.cols)
            if self.method=='lgb':
                clf=LGBMClassifier(*args, **kwargs)
            elif self.method=='xgb':
                clf=XGBClassifier(*args, **kwargs)
            clf.fit(self.xtrain[self.cols],self.xtrain[self.target])
            #test评估
            accscore=clf.score(self.xtest[self.cols],self.xtest[self.target])
            y_pred_prob=clf.predict_proba(self.xtest[self.cols])
            auc_score=roc_auc_score(self.xtest[self.target],y_pred_prob[:,1])
            fpr,tpr,thres=roc_curve(self.xtest[self.target],y_pred_prob[:,1])
            ks=max(tpr-fpr)
            #train评估
            accscore_train=clf.score(self.xtrain[self.cols],self.xtrain[self.target])
            y_pred_prob_train=clf.predict_proba(self.xtrain[self.cols])
            auc_score_train=roc_auc_score(self.xtrain[self.target],y_pred_prob_train[:,1])
            fpr_train,tpr_train,thres_train=roc_curve(self.xtrain[self.target],y_pred_prob_train[:,1])
            ks_train=max(tpr_train-fpr_train)
            acc_data.append(accscore)
            ks_data.append(ks)
            auc_data.append(auc_score)
            accdata_train.append(accscore_train)
            ksdata_train.append(ks_train)
            aucdata_train.append(auc_score_train)
            importances_data=pd.DataFrame()
            importances_data['var']=self.cols
            importances_data['importances']=clf.feature_importances_
            imp_data=importances_data[importances_data['importances']!=0]
            imp_data=imp_data.sort_values('importances',ascending=False)
            self.cols=imp_data['var'].head(round(len(imp_data)*self.stay_pct)).to_list()
            run_cnt.append(i+1)
            end = datetime.now()
            #print('已完成',i+1,'次迭代,','用时',round((end-start).seconds/60,2),'分钟,','剩余',num-i-1,'次迭代')
        js_data['run_cnt']=run_cnt
        js_data['staynum']=stay_colnum
        js_data['acc']=acc_data
        js_data['ks_test']=ks_data
        js_data['auc_test']=auc_data
        js_data['ks_train']=ksdata_train
        js_data['auc_train']=aucdata_train
        js_data['ks_change_pct']=js_data['ks_test'].pct_change(1).fillna(0)
        js_data['auc_change_pct']=js_data['auc_test'].pct_change(1).fillna(0)
        js_data['ks_train_test_dif']=js_data['ks_train']-js_data['ks_test']
        js_cols=cols_detail[js_data.sort_values('ks_test',ascending=False).head(5).sort_values('auc_change_pct',ascending=False).head(3).sort_values('ks_change_pct',ascending=False).head(2).sort_values('run_cnt',ascending=False).head(1).index[0]]
        top=js_data.sort_values('ks_test',ascending=False).head(5).staynum.reset_index(drop=True)
        plt.figure(figsize=(14,7))
        plt.subplot(221)
        plt.plot(js_data['staynum'][1:],js_data['ks_test'][1:])
        plt.xlabel('var_num')
        plt.ylabel('ks_test')
        plt.axvline(top[0],linestyle='--',color='r')
        plt.axvline(top[1],linestyle='--',color='r')
        plt.axvline(top[2],linestyle='--',color='r')
        plt.axvline(top[3],linestyle='--',color='r')
        plt.axvline(top[4],linestyle='--',color='r')
        plt.subplot(222)
        plt.plot(js_data['staynum'][1:],js_data['ks_change_pct'][1:])
        plt.xlabel('var_num')
        plt.ylabel('ks_change_pct')
        plt.axvline(top[0],linestyle='--',color='r')
        plt.axvline(top[1],linestyle='--',color='r')
        plt.axvline(top[2],linestyle='--',color='r')
        plt.axvline(top[3],linestyle='--',color='r')
        plt.axvline(top[4],linestyle='--',color='r')
        plt.subplot(223)
        plt.plot(js_data['staynum'][1:],js_data['auc_test'][1:])
        plt.xlabel('var_num')
        plt.ylabel('auc_test')
        plt.axvline(top[0],linestyle='--',color='r')
        plt.axvline(top[1],linestyle='--',color='r')
        plt.axvline(top[2],linestyle='--',color='r')
        plt.axvline(top[3],linestyle='--',color='r')
        plt.axvline(top[4],linestyle='--',color='r')
        plt.subplot(224)
        plt.plot(js_data['staynum'][1:],js_data['auc_change_pct'][1:])
        plt.xlabel('var_num')
        plt.ylabel('auc_change_pct')
        plt.axvline(top[0],linestyle='--',color='r')
        plt.axvline(top[1],linestyle='--',color='r')
        plt.axvline(top[2],linestyle='--',color='r')
        plt.axvline(top[3],linestyle='--',color='r')
        plt.axvline(top[4],linestyle='--',color='r')
        plt.savefig(self.path+'/'+self.method+'_js.png',bbox_inches='tight')
        plt.show()
        end_total = datetime.now()
        print('共用时',round((end_total-start_total).seconds/60,2),'分钟')
        self.cols=self.cols_copy
        js_data.to_excel(self.path+'/js_recu_data_'+self.method+'.xlsx',index=None)
        joblib.dump(cols_detail,self.path+'/js_cols_detail_'+self.method+'.pkl')
        joblib.dump(js_cols,self.path+'/js_select_cols_'+self.method+'.pkl')
        print("---------------------------------------------")
        return js_data,cols_detail,js_cols