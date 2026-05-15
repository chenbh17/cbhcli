import paramiko
import pandas as pd
import numpy as np
from datetime import *
import warnings
warnings.filterwarnings("ignore")


def con_linux(shell,user='chenbh17'):
    if user=='chenbh17':
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname='192.168.10.100', port=22, username='chenbh17', password="''''")
        shell_list=shell.split(';')
        for j,i in enumerate(shell_list):
            shell_list[j]=shell_list[j].strip()#删除换行符
        for i in range(len(shell_list))[::-1]:#既然删除元素之后list后面元素向前移动，而且长度变短，那么为何不从list的最后一个开始倒序删除
            if len(shell_list[i])==0 or len(shell_list[i])==1:
                shell_list.remove(shell_list[i])
        #print('共',len(shell_list),'段linux shell代码')
        for i,j in enumerate(shell_list):
            ssh_in,ssh_out,ssh_error = ssh.exec_command(j)#ssh_in 标准输入,也就是我们输入的命令,ssh_out 标准输出,命令执行的结果,ssh_error 命令执行过程中的错误
            #读取结果
            res,error = ssh_out.read(),ssh_error.read()
            result = res if res else error
            #print('第'+str(i+1)+'段代码结果：')
            print(result.decode())
        ssh.close()
        return None
    elif user=='root':
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname='192.168.10.100', port=22, username='root', password='root')
        print('连接成功!')
        shell_list=shell.split(';')
        for j,i in enumerate(shell_list):
            shell_list[j]=shell_list[j].strip()#删除换行符
        for i in range(len(shell_list))[::-1]:#既然删除元素之后list后面元素向前移动，而且长度变短，那么为何不从list的最后一个开始倒序删除
            if len(shell_list[i])==0 or len(shell_list[i])==1:
                shell_list.remove(shell_list[i])
        #print('共',len(shell_list),'段linux shell代码')
        for i,j in enumerate(shell_list):
            ssh_in,ssh_out,ssh_error = ssh.exec_command(j)#ssh_in 标准输入,也就是我们输入的命令,ssh_out 标准输出,命令执行的结果,ssh_error 命令执行过程中的错误
            #读取结果
            res,error = ssh_out.read(),ssh_error.read()
            result = res if res else error
            #print('第'+str(i+1)+'段代码结果：')
            print(result.decode())
        ssh.close()
        return None


def data_trans_linux(local_loc=None,client_loc=r'/media/chenbh17/cbhssd/invest/data/to_hive.csv',method='put'):
    #print('windows-linux文件转入传出时，文件路径前必须加r，并精确到文件名和后缀')
    # 连接服务器
    transport = paramiko.Transport(('192.168.10.100',22))
    transport.connect(username='chenbh17',password="''''")
    ftp = paramiko.SFTPClient.from_transport(transport)  # 定义一个ftp实例
    if method=='put':
        ftp.put(local_loc,client_loc)  # 上传文件
    elif method=='get':
        ftp.get(client_loc,local_loc)   # 下载文件
    ftp.close()
    transport.close()
    return None
    
    
def jps():
    shell='cd /opt/module/jdk8/bin/ && ./jps'
    con_linux(shell)

def hadoop(type):
    if type=='start':
        shell='cd /opt/install/hadoop/sbin/ && ./start-all.sh'
        con_linux(shell)
        return 'hadoop启动成功'
    elif type=='stop':
        shell='cd /opt/install/hadoop/sbin/ && ./stop-all.sh'
        con_linux(shell)
        return 'hadoop关闭成功'

def start_hive():
    shell='cd /opt/module/hive/bin/ && nohup hive --service metastore 2>&1 &;cd /opt/module/hive/bin/ && nohup hive --service hiveserver2 2>&1 &'
    con_linux(shell)
    return 'hive启动成功'