# -*- coding: utf-8 -*-
"""
Created on Fri Jan 26 14:22:47 2018

@author: Bin
"""

import numpy as np
import pandas as pd
import tensorflow as tf
import queue
from kafka import KafkaConsumer
import time, threading
import sys
import math
import matplotlib.pyplot as plt
sys.path.insert(0, 'C:/Users/Bin/Desktop/Thesis/code')
from EncDecAD_Pred import EncDecAD_Pred
from Conf_Prediction_KDD99 import Conf_Prediction_KDD99
from LocalPreprocessing import LocalPreprocessing
from EncDecAD_ReTrain import EncDecAD_ReTrain

conf = Conf_Prediction_KDD99()
batch_num =conf.batch_num
step_num = conf.step_num
MIN_TEST_BLOCK_NUM = conf.min_test_block_num
MIN_RETRAIN_BLOCK_NUM = conf.min_retrain_block_num
class_label_file = conf.class_label_path
#class_label_file = "C:/Users/Bin/Documents/Datasets/KDD99/classes.txt"

kafka_topic = 'kdd99stream'
g_id='test-consumer-group'
servers = ['localhost:9092']
offset = "latest"#"earliest"

consumer = KafkaConsumer(kafka_topic,
                         group_id=g_id,    # defined in consumer.properties file
                         bootstrap_servers=servers,
                         auto_offset_reset = offset)
consumer.poll()
#go to end of the stream
consumer.seek_to_end()
dataframe = pd.DataFrame()
lock = threading.BoundedSemaphore(value=1)

# use for store relation between pred and lables
with open(class_label_file) as file:
    line = file.readline()
    class_labels = pd.Series(line.split(","),name="label")
    class_labels = class_labels[class_labels!="normal"].reset_index(drop=True)
class_pred_relation = pd.DataFrame(np.zeros(class_labels.size*2).reshape(-1,2),columns=['False alarm','True alarm'])# two columns for a_as_n  and a_as_a
class_pred_relation = pd.concat((class_labels,class_pred_relation),axis=1)
class_pred_relation.label = class_pred_relation.label.apply(str)

def block_generator2queue(q,stop_event):
    
    while not stop_event.is_set():
#        print("Thread: block_generator2queue\n\n")
        block = []
        try:
            for message in consumer:
                if stop_event.is_set():
                    break
                row = message.value.decode("utf-8") 
                row_tmp = row.split(",")
                row_tmp[-1] = row_tmp[-1].strip("\".")
                block.append(row_tmp)
                if len(block)==batch_num*step_num:
                    df = pd.DataFrame(np.array(block))
                    q.put(df)
                    block.clear()
        finally:
            consumer.close()
                
                
def read_block_from_queue(q,stop_event):
    global dataframe
    
    while not stop_event.is_set():
#        print("Thread: read_block_from_queue\n\n")
        if q.empty() == False:
            b = q.get()
#            if b.shape[1] <50:   #hardcode         
            if dataframe.size == 0:
                dataframe = b
               
            else:    
                df_tmp = pd.concat((dataframe,b),axis=0).reset_index(drop=True)                  
                if df_tmp.shape[1] != b.shape[1] : 
                    print("Warn: Strange dataframe shape, dropped.") 
                else:
                    dataframe = df_tmp
        else :
            time.sleep(0.5)
            
def prediction(stop_event,results_list):
    global dataframe
    global class_pred_relation
    print("Thread: prediction\n\n")
    pred = EncDecAD_Pred(conf)
    local_preprocessing = LocalPreprocessing(conf.column_name_file ,conf.step_num)
    #  reload model
    sess = tf.Session()
    input_,output_,p_input,p_is_training,loss_,train_,mu,sigma,threshold= pred.reloadModel(sess)
    p_inputs = [tf.squeeze(t, [1]) for t in tf.split(p_input, step_num, 1)]
    print("LSTMs-Autoencoder Model reloaded.")
    buffer = [] # for collecting hard examples used for retraining model
    while not stop_event.is_set():   
#        with lock:
            lock.acquire()
            if dataframe.index.size < batch_num*step_num*MIN_TEST_BLOCK_NUM:
                sec = 5
                print("Currently not enough data for prediction, ",dataframe.index.size,"/",batch_num*step_num*MIN_TEST_BLOCK_NUM)
                lock.release()
                time.sleep(sec)
                
            else:
                try:
                    print("Local preprocessing...")
                    #After preprocessing, the second to last col is the string class label
                    # and last col is the 0/1 grundtruth (1 stand for anomaly)
                    lpdf = dataframe
                    index = dataframe.iloc[:,0]
                    index = pd.to_numeric(index, errors='coerce')
                    dataframe_preprocessed = local_preprocessing.run(lpdf, for_training = False)    
                    
                    print("Making prediction...")
                    dataframe_preprocessed.reset_index(drop=True,inplace=True)
                    dataset = dataframe_preprocessed.iloc[:,:-2]
                    label = dataframe_preprocessed.iloc[:,-1]
                    class_list = dataframe_preprocessed.iloc[:,-2]
               
                    # window.size == step_num
                    
                    hard_example_window_index, results= pred.predict(dataset,index,label,sess,input_,output_,p_input,p_is_training,mu,sigma,threshold)
                    # results : [alarm_accuracy,false_alarm,alarm_recall,pred]
                    
                    # store pred & label relation                    
                    predictions = pd.Series(results[3])
                    for p in range(predictions.size):
                        if predictions[p] == 0: continue
                        elif label[p] == 1: # a_as_a
                            i = class_pred_relation[class_pred_relation.label == class_list[p]].index
                            class_pred_relation.iloc[i,-1] += 1 
                        else:  # a_as_n
                            i = class_pred_relation[class_pred_relation.label == class_list[p]].index
                            class_pred_relation.iloc[i,-2] += 1                  
                    
                    results_list.append(results)
                    # got hard examples' index from prediction, then using this index to find the UNpreprocessed 
                    #hard examples from the original dataframe 
                    buffer.append(lpdf.loc[hard_example_window_index])
#                    print("A df with %d rows is added to Buffer."%lpdf.index.size)
                    buffer_data_len = sum([df_.shape[0] for df_ in buffer])
                    if buffer_data_len >= MIN_RETRAIN_BLOCK_NUM*batch_num:
                            print("It's time to Re-Training model.")                          
                            data_for_retrain = pd.concat(buffer,axis=0)
                            data_for_retrain.reset_index(drop=True,inplace=True)
#                            print("data_for_retrain.shape: ",data_for_retrain.shape)
                            #retrain dataset shape: (batch_num*step_num*MIN_RETRAIN_BLOCK_NUM,elem_num)
                            data_for_retrain = data_for_retrain.iloc[:data_for_retrain.index.size-data_for_retrain.index.size%batch_num,:]#buffer[0].shape[1]]#.....................
                            sn,vn1,vn2,tn,va,ta = local_preprocessing.run(data_for_retrain, for_training = True)
                            
                            
    #                        if min(sn.size,vn1.size,vn2.size,tn.size,va.size,ta.size) == 0:
                            if min([x.index.size for x in [sn,vn1,vn2,va]])<batch_num*step_num:
                                
                                print("Not enough normal or anomaly data for retraining, still waiting for more data.")
                                print("sn(%d), vn1(%d), vn2(%d), va(%d) batches."%(sn.index.size//step_num//batch_num,vn1.index.size//step_num//batch_num,vn2.index.size//step_num//batch_num,va.index.size//step_num//batch_num))
                                print("Retrain Buffer: %d/%d.\n"%(buffer_data_len,MIN_RETRAIN_BLOCK_NUM*batch_num))
                                dataframe = pd.DataFrame()
                                continue
                            print("Re-Training Model...")
                            print("sn(%d), vn1(%d), vn2(%d), va(%d) batches."%(sn.index.size//step_num//batch_num,vn1.index.size//step_num//batch_num,vn2.index.size//step_num//batch_num,va.index.size//step_num//batch_num))
                            index_of_data_for_retrain = [i.iloc[:,0] for i in [sn,vn1,vn2,tn,va,ta]]
#                            for df_for_retrain in [sn,vn1,vn2,tn,va,ta]:
#                                df_for_retrain = df_for_retrain.iloc[:,1:]                        
                            sn = sn.drop(sn.columns[[0]],axis=1)
                            vn1 = vn1.drop(vn1.columns[[0]],axis=1)
                            vn2 = vn2.drop(vn2.columns[[0]],axis=1)
                            tn = tn.drop(tn.columns[[0]],axis=1)
                            va = va.drop(va.columns[[0]],axis=1)
                            ta = ta.drop(ta.columns[[0]],axis=1)
                            retrain = EncDecAD_ReTrain(sn,vn1,vn2,tn,va,ta)
                            mu_new,sigma_new,threshold_new = retrain.continue_training(sess,loss_, train_,p_input,p_inputs,p_is_training,input_,output_)
                            if math.isnan(threshold_new ) == False:
                                mu,sigma,threshold = mu_new,sigma_new,threshold_new
                            buffer.clear()
                    else: 
                        
                        print("Retrain Buffer: %d/%d.\n"%(buffer_data_len,MIN_RETRAIN_BLOCK_NUM*batch_num))
                        print("Finish prediction.Waiting for next batches of data.")
                    
                finally:
                    dataframe = pd.DataFrame()
                    lock.release()

def drawing():             
    global class_pred_relation  
    class_pred_relation.iloc[:,1:].plot.bar(figsize=(13,6))
    plt.xticks(class_pred_relation.index, class_pred_relation.label, rotation='vertical')
    plt.title("Prediction statistic according to class label")
    plt.xlabel("Anomalous classes")
    plt.ylabel("Count")
    plt.show()
    
    
def main():
    q = queue.Queue()
    stop_event = threading.Event()
    results_list = []
    write = threading.Thread(target=block_generator2queue, name='WriteThread',args=(q,stop_event,))
    read = threading.Thread(target=read_block_from_queue, name='ReadThread',args=(q,stop_event,))
    predict = threading.Thread(target=prediction, name='Prediction',args=(stop_event,results_list))
    draw = threading.Thread(target=drawing, name='Plotting',args=())
    try:
        
        write.start()
        read.start()
        predict.start()
        
        while 1:
            time.sleep(.1)
    except (KeyboardInterrupt,SystemExit):
        draw.start()
        draw.join()
        stop_event.set()
        print("Threads closed.")
        
if __name__=="__main__":
    main()

    

    
