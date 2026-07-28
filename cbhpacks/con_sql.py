import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings("ignore")
import pymysql
from datetime import *
from pyhive import hive
from datetime import *
import traceback
from cbhpacks.con_linux import *
from tqdm import *
#engine = create_engine("数据库类型+数据库驱动://数据库用户名:数据库密码@IP地址:端口号/数据库?编码...", 其它参数)
#engine = create_engine('hive://hadoop:hadoop@192.168.10.200:10000/stock?auth=CUSTOM')
#engine = create_engine("mysql+pymysql://root:root@192.168.10.200:3306/zjrc")--mysql
#conn = conn=engine.connect() 
#data_cus.to_sql(name='customer_py',con=conn,index=False)
import clickhouse_connect
import pandas as pd
import numpy as np

def chrun(sql):
    client = clickhouse_connect.get_client(
        host='192.168.10.100',  # ClickHouse服务器IP
        port=8123,              # HTTP端口，默认为8123
        username='admin',     # 用户名
        password='123456', # 密码
        database='pro'      # 默认数据库
    )
    start = datetime.now()
    sql_list=sql.split(';')
    for j,i in enumerate(sql_list):
        sql_list[j]=sql_list[j].strip()#删除换行符
    for i in range(len(sql_list))[::-1]:#既然删除元素之后list后面元素向前移动，而且长度变短，那么为何不从list的最后一个开始倒序删除
        if len(sql_list[i])==0 or len(sql_list[i])==1:
            sql_list.remove(sql_list[i])
    for j,i in enumerate(sql_list):
        client.query(i)
    end=datetime.now()
    print("运行时间: ",round((end-start).seconds/60,2),'分钟')
    return 'done'


def chdf(sql):
    client = clickhouse_connect.get_client(
        host='192.168.10.100',  # ClickHouse服务器IP
        port=8123,              # HTTP端口，默认为8123
        username='admin',     # 用户名
        password='123456', # 密码
        database='pro'      # 默认数据库
    )
    start = datetime.now()
    sql_list=sql.split(';')
    for j,i in enumerate(sql_list):
        sql_list[j]=sql_list[j].strip()#删除换行符
    for i in range(len(sql_list))[::-1]:#既然删除元素之后list后面元素向前移动，而且长度变短，那么为何不从list的最后一个开始倒序删除
        if len(sql_list[i])==0 or len(sql_list[i])==1:
            sql_list.remove(sql_list[i])
    df_details=[]
    for j,i in enumerate(sql_list):
        df = client.query_df(i)
        df_details.append(df)
    end=datetime.now()
    print("运行时间: ",round((end-start).seconds/60,2),'分钟')
    if len(df_details)==1:
        return df
    elif len(df_details)>1:
        return df_details
    else:
        return None

def con_mysql(sql=None,host='192.168.10.200', port=3306,user='hive', password='hive',database='dev',charset=None):
    #可写多段sql代码，如果要输出多表，则返回list，list中包含所有dataframe，若只需一个表，则返回dataframe
    conn = pymysql.connect(host=host,port=port, user=user, password=password,database=database,charset=charset)  # 服务器名,账户,密码,数据库名,编码方式
    start = datetime.now()
    sql_list=sql.split(';')
    for j,i in enumerate(sql_list):
        sql_list[j]=sql_list[j].strip()#删除换行符
    for i in range(len(sql_list))[::-1]:#既然删除元素之后list后面元素向前移动，而且长度变短，那么为何不从list的最后一个开始倒序删除
        if len(sql_list[i])==0 or len(sql_list[i])==1:
            sql_list.remove(sql_list[i])
    #print('共',len(sql_list),'段sql代码')
    df_details=[]
    cursor = conn.cursor()
    for j,i in enumerate(sql_list):
        print('正在运行第',j+1,'段代码')  
        try:
            cursor.execute(i)
            conn.commit()
        except:
            print('>>>>>>>>>>>>>>>>第',j+1,'段sql语句报错，报错信息如下：<<<<<<<<<<<<<<')
            error=traceback.format_exc()
            print(error)
            return None
        try:
            df=pd.read_sql(sql=i,con=conn)
            df_details.append(df)
        except:
            pass
    end=datetime.now()
    conn.close()
    print("运行时间: ",round((end-start).seconds/60,2),'分钟')
    if len(df_details)==1:
        return df
    elif len(df_details)>1:
        return df_details
    else:
        return None

def con_hive(sql=None,host='192.168.10.100',port=10000,auth='CUSTOM',database='pro',username='hive',password='hive'):
    #可写多段sql代码，如果要输出多表，则返回list，list中包含所有dataframe，若只需一个表，则返回dataframe
    conn = hive.Connection(host=host,port=port,auth=auth,database=database,username=username,password=password)  # 服务器名,账户,密码,数据库名
    start = datetime.now()
    sql_list=sql.split(';')
    for j,i in enumerate(sql_list):
        sql_list[j]=sql_list[j].strip()#删除换行符
    for i in range(len(sql_list))[::-1]:#既然删除元素之后list后面元素向前移动，而且长度变短，那么为何不从list的最后一个开始倒序删除
        if len(sql_list[i])==0 or len(sql_list[i])==1:
            sql_list.remove(sql_list[i])
    #print('共',len(sql_list),'段sql代码')
    df_details=[]
    cursor = conn.cursor()
    for j,i in enumerate(sql_list):
        #print('正在运行第',j+1,'段代码')  
        if i[:4]=='load':
            print('loading data into table.....')
            try:
                cursor.execute(i)
                conn.commit()
            except:
                print('>>>>>>>>>>>>>>>>第',j+1,'段sql语句报错，报错信息如下：<<<<<<<<<<<<<<')
                error=traceback.format_exc()
                try:
                    error=error.split(', errorMessage=')[-1].split('"')[1]
                except:
                    error=error.split(', errorMessage=')[-1].split("'")[1]
                print(error)
        elif i[:6]=='insert':
            print('inserting data into table.....')
            try:
                cursor.execute(i)
                conn.commit()
            except:
                print('>>>>>>>>>>>>>>>>第',j+1,'段sql语句报错，报错信息如下：<<<<<<<<<<<<<<')
                error=traceback.format_exc()
                try:
                    error=error.split(', errorMessage=')[-1].split('"')[1]
                except:
                    error=error.split(', errorMessage=')[-1].split("'")[1]
                print(error)
        else:
            print('hive operating...')
            try:
                cursor.execute(i)
                conn.commit()
            except:
                print('>>>>>>>>>>>>>>>>第',j+1,'段sql语句报错，报错信息如下：<<<<<<<<<<<<<<')
                error=traceback.format_exc()
                try:
                    error=error.split(', errorMessage=')[-1].split('"')[1]
                except:
                    error=error.split(', errorMessage=')[-1].split("'")[1]
                print(error)
                return None
            try:
                df=pd.read_sql(sql=i,con=conn)
                for i in list(df.columns):
                    if len(i.split('.'))==2:
                        col_name=i.split('.')[1]
                        df.rename(columns={i:col_name},inplace=True)
                df_details.append(df)
            except:
                pass
    end=datetime.now()
    conn.close()
    print("运行时间: ",round((end-start).seconds/60,2),'分钟')
    if len(df_details)==1:
        return df
    elif len(df_details)>1:
        return df_details
    else:
        return None
        


def get_create_table(data,table_name,encoding=None,partition=None,bucket=None,partition_col=None,bucket_col=None,bucket_num=10):
    #目前只支持int、float、object类型，对应的sql格式为INT、DOUBLE、STRING，table_name要包含库名，可选择encoding='GBK'编码默认utf-8编码，目前只支持csv格式
    col_list=[]
    for i in list(data.columns):
        if data[i].dtype=='int64' or data[i].dtype=='int' or data[i].dtype=='int32':
            col_list.append(' '+i+' int,')
        elif data[i].dtype=='float' or data[i].dtype=='float64' or data[i].dtype=='float32':
            col_list.append(' '+i+' double,')
        elif data[i].dtype=='O':
            col_list.append(' '+i+' string,')
    if partition :
        if bucket:
            col_list.remove(' '+partition_col+',')
            col_list=''.join(col_list)
            col_list=col_list[:len(col_list)-1]
            if encoding=='GBK':
                create_sql='drop table if exists '+table_name+'; create table '+table_name+' ('+col_list+") partitioned by ("+partition_col+")"+" CLUSTERED BY ("+bucket_col+") INTO "+str(bucket_num)+" BUCKETS"+" row format delimited fields terminated by ',';"+'alter table '+table_name+" set SERDEPROPERTIES ('serialization.encoding'='"+encoding+"');"
            else:
                create_sql='drop table if exists '+table_name+'; create table '+table_name+' ('+col_list+") partitioned by ("+partition_col+")"+" CLUSTERED BY ("+bucket_col+") INTO "+str(bucket_num)+" BUCKETS"+" row format delimited fields terminated by ',';"
        else: 
            col_list.remove(' '+partition_col+',')
            col_list=''.join(col_list)
            col_list=col_list[:len(col_list)-1]
            if encoding=='GBK':
                create_sql='drop table if exists '+table_name+'; create table '+table_name+' ('+col_list+") partitioned by ("+partition_col+")"+" row format delimited fields terminated by ',';"+'alter table '+table_name+" set SERDEPROPERTIES ('serialization.encoding'='"+encoding+"');"
            else:
                create_sql='drop table if exists '+table_name+'; create table '+table_name+' ('+col_list+") partitioned by ("+partition_col+")"+" row format delimited fields terminated by ',';"
    else:
        col_list=''.join(col_list)
        col_list=col_list[:len(col_list)-1]
        if encoding=='GBK':
            create_sql='drop table if exists '+table_name+'; create table '+table_name+' ('+col_list+") row format delimited fields terminated by ',';"+'alter table '+table_name+" set SERDEPROPERTIES ('serialization.encoding'='"+encoding+"');"
        else:
            create_sql='drop table if exists '+table_name+'; create table '+table_name+' ('+col_list+") row format delimited fields terminated by ',';"
    return create_sql

def to_hive(data,table_name,local_loc,shell_loc='/media/chenbh17/cbhssd/invest/data/'
           ,method='overwrite',encoding='UTF-8',partition=None,bucket=None,partition_col='year string',bucket_col=None,bucket_num=10):
    #step0:将python中的表保存到本地路径(local_loc)中
    if partition:
        par_col=partition_col.split(' ')[0]
        cols=data.columns.to_list()
        data=data[cols[:cols.index(par_col)]+cols[cols.index(par_col)+1:]+[par_col]]
        if method=='overwrite':
            sql=get_create_table(data=data,table_name=table_name,encoding=encoding,partition=partition,bucket=bucket,partition_col=partition_col,bucket_col=bucket_col,bucket_num=bucket_num)
            con_hive(sql)
            for key,grp in data.groupby(par_col):
                data=grp
                data.to_csv(local_loc,index=False,encoding=encoding)
                #print('step1:数据表'+table_name+'建立完成')
                #print('step2:将csv数据导入到指定文件夹')
                data_trans_linux(local_loc)
                #print('step3:在指定文件夹(shell_loc)中将csv数据第一行列名去掉，以便在hive插入数据')
                shell='cd '+shell_loc+' && '+"sed '1d' to_hive.csv > new_to_hive.csv"
                con_linux(shell)
                #print('step4:将linux中的数据覆盖到hive刚刚建的空表里')
                sql="load data local inpath '/media/new_to_hive.csv' into table "+table_name+f' partition ({par_col}={str(key)});'#/media/是docker中docker-hive-hive-server-1容器的路径，挂在了主机路径/media/chenbh17/cbhssd/invest/data/
                con_hive(sql)
                #print('step5:将linux指定文件夹中的数据删除')
                shell='cd '+shell_loc+' && '+'rm to_hive.csv new_to_hive.csv'
                con_linux(shell)
            return '数据表导入hive成功!'
        elif method=='append':
            for key,grp in data.groupby(par_col):
                data=grp
                data.to_csv(local_loc,index=False,encoding=encoding)
                #print('step1:将csv数据导入到指定文件夹')
                data_trans_linux(local_loc)
                #print('step2:在指定文件夹(shell_loc)中将csv数据第一行列名去掉，以便在hive插入数据')
                shell='cd '+shell_loc+' && '+"sed '1d' to_hive.csv > new_to_hive.csv"
                con_linux(shell)
                #print('step3:将linux中的数据添加拼接到hive临时表里')
                sql="load data local inpath '/media/new_to_hive.csv' into table "+table_name+f' partition ({par_col}={str(key)});'
                con_hive(sql)
                #print('step4:将linux指定文件夹中的数据删除')
                shell='cd '+shell_loc+' && '+'rm to_hive.csv new_to_hive.csv'
                con_linux(shell)
            return '数据表更新成功!'
    else:
        data.to_csv(local_loc,index=False,encoding=encoding)
        if method=='overwrite':
            sql=get_create_table(data=data,table_name=table_name,encoding=encoding,partition=partition,partition_col=partition_col)
            con_hive(sql)
            #print('step1:数据表'+table_name+'建立完成')
            #print('step2:将csv数据导入到指定文件夹')
            data_trans_linux(local_loc)
            #print('step3:在指定文件夹(shell_loc)中将csv数据第一行列名去掉，以便在hive插入数据')
            shell='cd '+shell_loc+' && '+"sed '1d' to_hive.csv > new_to_hive.csv"
            con_linux(shell)
            #print('step4:将linux中的数据覆盖到hive刚刚建的空表里')
            sql="load data local inpath '"+'/media/'+"new_to_hive.csv' "+method+' into table '+table_name+';'
            con_hive(sql)
            #print('step5:将linux指定文件夹中的数据删除')
            shell='cd '+shell_loc+' && '+'rm to_hive.csv new_to_hive.csv'
            con_linux(shell)
            return '数据表导入hive成功!'
        elif method=='append':
            #print('step1:将csv数据导入到指定文件夹')
            data_trans_linux(local_loc)
            #print('step2:在指定文件夹(shell_loc)中将csv数据第一行列名去掉，以便在hive插入数据')
            shell='cd '+shell_loc+' && '+"sed '1d' to_hive.csv > new_to_hive.csv"
            con_linux(shell)
            #print('step3:将linux中的数据添加拼接到hive临时表里')
            sql="load data local inpath '"+'/media/'+"new_to_hive.csv' "+''+' into table '+table_name+';'
            con_hive(sql)
            #print('step4:将linux指定文件夹中的数据删除')
            shell='cd '+shell_loc+' && '+'rm to_hive.csv new_to_hive.csv'
            con_linux(shell)
            return '数据表更新成功!'
#to_hive(data=df,table_name='stock.cbh_try',local_loc=r'E:\data\target\mth_change_flg_try.csv',encoding='GBK',partition=True,partition_col='year string')

def rfms_sql(data,cols,new_table,origin_table,day_list=[5,20,120,250]):
    #print('此函数用于rfms范式的特征衍生，默认时间间隔为5 20 120 250四种')
    list_bina=['sum','avg','stddev']
    list_double=['sum','avg','stddev','min','max']
    drop_sql=' drop table if exists '+new_table+'; '
    create_sql=' create table '+new_table+' stored as orc as '
    select_sql=' select stock_code, trade_date, mth, year '
    bina_sql=[]
    double_sql=[]
    print("正在进行二分类特征衍生")
    for days in tqdm(day_list):
        for bina in list_bina:
            for i in cols:
                if len(data[i].value_counts())==2:
                    bina_sql.append('    ,'+bina+'('+i+')'+' over(partition by stock_code order by trade_date rows between '+str(days-1)+' preceding and current row) as '+i+'_l'+str(days)+'d_'+bina)
    bina_sql=' '.join(bina_sql)
    print("正在进行其他特征衍生")
    for days in tqdm(day_list):   
        for double in list_double:
            for i in cols:
                if len(data[i].value_counts())!=2:
                    double_sql.append('    ,'+double+'('+i+')'+' over(partition by stock_code order by trade_date rows between '+str(days-1)+' preceding and current row) as '+i+'_l'+str(days)+'d_'+double)
    double_sql=' '.join(double_sql)
    from_sql=' from '+origin_table+' '
    order_sql=' order by stock_code,trade_date '
    sql_all=drop_sql+create_sql+select_sql+bina_sql+double_sql+from_sql+order_sql
    try:
        con_hive(sql_all)
        return 'rfms范式特征衍生操作完成，结果已保存在hive中，表名为：'+new_table
    except:
        return 'con_hive报错'

