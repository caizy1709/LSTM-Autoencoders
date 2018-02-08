# -*- coding: utf-8 -*-
"""
Created on Fri Jan 26 13:29:32 2018

@author: Bin
"""
import time
from kafka import KafkaProducer
import pandas as pd

filename = "C:/Users/Bin/Documents/Datasets/KDD99/6_subsets_win/test_with_label.csv"

producer = KafkaProducer(bootstrap_servers='localhost:9092')
# column names, is_guest_login & dis_host_login & logged_in & land & flag & service & protocol_type
with open("C:/Users/Bin/Documents/Datasets/KDD99/columns.txt") as col_file:
    line = col_file.readline()
    #line = line.replace('.',',')
    columns = line.split('.')
    col_names = []
    col_types = []
    for col in columns:
        col_names.append(col.split(': ')[0].strip())
        col_types.append(col.split(': ')[1])
    col_names.append("label")



chunksize = 10000
http = []
smtp = []
count =0
for chunk in pd.read_csv(filename,names=col_names, chunksize=chunksize):
    for index,row in chunk.iterrows():
        message = row.to_json(orient="split").split("data\":[")[1].strip("\"]}'").encode()
        producer.send('kdd99stream', message)
        print(message)
        count +=1
        time.sleep(0.25)
#        if count%400 == 0:
#            print("stream sleeping for 15 sec.\n")
#            time.sleep(15)
        
        
        
#        if row.service == 'http':
#            http.append(row)
#        elif row.service == 'smtp':
#            smtp.append(row)

#http = pd.DataFrame(np.array(http))
#smtp = pd.DataFrame(np.array(smtp))    
#
#http = http.reset_index(drop=True)
#smtp = smtp.reset_index(drop=True)
#foo = pd.concat((smtp,http),axis=0)
#foo = foo.reset_index(drop=True)
#
#bar = smtp[smtp.iloc[:,-1]!="normal."]
#
#
#http.to_csv("C:/Users/Bin/Documents/Datasets/KDD99/http.csv")
#smtp.to_csv("C:/Users/Bin/Documents/Datasets/KDD99/smtp.csv")
#foo.to_csv("C:/Users/Bin/Documents/Datasets/KDD99/smtp+http.csv")
#       