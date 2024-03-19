#!/usr/bin/env python
# coding: utf-8

# # MACHINE LEARNING CHALLENGE

# # TASK 1

# # MODEL 1

# In[ ]:


# Import libraries
import pandas as pd
import numpy as np
import seaborn as sns
import matplotlib.pyplot as plt
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix
from sklearn.preprocessing import LabelBinarizer
from sklearn.metrics import accuracy_score
from random import randrange
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import RepeatedStratifiedKFold
from scipy.stats import loguniform
from sklearn.model_selection import RandomizedSearchCV
from sklearn.model_selection import GridSearchCV
from sklearn.metrics import precision_score
from sklearn.metrics import recall_score
from sklearn import metrics

# Load in and explore data

with np.load('train_data_label.npz') as data:
    train_data = data['train_data']
    train_label = data['train_label']
    
with np.load('test_data_label.npz') as data:
    test_data = data['test_data']
    test_label = data['test_label']

print(len(train_data))
print(len(test_data))

train_data[0][1:10]

train_label

len(test_data)

# count plot for visualizing target label distribution
plt.figure(figsize = (18, 8))
sns.countplot(train_label)

plt.figure(figsize = (18, 8))
sns.countplot(test_label)

# look at images from training data
def show_image(n):
    plt.imshow(train_data[n].reshape(28, 28))
    print(train_label[n])
    
show_image(randrange(27455))

# Look at images from testing data, different at all?
def show_image2(n):
    plt.imshow(test_data[n].reshape(28, 28))
    print(test_label[n])
    
show_image2(randrange(7172))


# In[ ]:


# Find optimal parameters using grid search

# split training data into training and validation
x_train, x_val, y_train, y_val = train_test_split(train_data, train_label, test_size = 0.2)

# define the model
model = LogisticRegression()

# define the evaluation
cv = RepeatedStratifiedKFold(n_splits=10, n_repeats=3, random_state=1)

# define the search space
space = dict()
space['solver'] = ['lbfgs']
space['penalty'] = ['l1', 'l2']
space['C'] = [0.2, 1, 5, 25, 100]

# define the search
search = GridSearchCV(model, space, cv = cv, scoring='accuracy', n_jobs=-1)

# execute search
result = search.fit(x_train, y_train)
# summarize result
print('Best Score: %s' % result.best_score_)
print('Best Hyperparameters: %s' % result.best_params_)

# Build the best model

# build model based on paramters found in grid search and intuition
lg = LogisticRegression(solver = 'lbfgs', max_iter = 2500, penalty = 'l2')
lg.fit(x_train, y_train)

# make predictions on validation data using model
val_pred = lg.predict(x_val)
val_pred



# Model evaluation

# compute accuracy on training set
scores = lg.score(x_train, y_train)
print("Training Accuracy: " + str(scores))

# compute eval metrics on validation set
print("Validation Accuracy: " + str(accuracy_score(val_pred, y_val)))
print("Precision: " + str(precision_score(val_pred, y_val, average = 'micro')))
print("Recall: " + str(recall_score(val_pred, y_val, average = 'micro')))

# auc roc curve
y_pred_proba = lg.predict_proba(x_val)[::,1]
fpr, tpr, _ = metrics.roc_curve(y_val, y_pred_proba, pos_label = 1)
y_pred_proba = np.expand_dims(y_pred_proba, axis = 1)
auc = metrics.auc(fpr, tpr)
plt.plot(fpr,tpr,label="AUC="+str(auc))
plt.ylabel('True Positive Rate')
plt.xlabel('False Positive Rate')
plt.legend(loc=4)
plt.show()

# Generalisation on test data

# run predictions on test data
test_pred = lg.predict(test_data)

print('Accuracy: {:.2f}'.format(accuracy_score(test_label, test_pred)))
print('Error rate: {:.2f}'.format(1 - accuracy_score(test_label, test_pred)))

# plot confusion matrix
confusion_matrix = confusion_matrix(test_label, test_pred, labels = [0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12,
                                                                    13, 14, 15, 16, 17, 18, 19, 20, 21, 22,
                                                                    23, 24])
plt.matshow(confusion_matrix, cmap=plt.cm.afmhot)
plt.show()

# alternative confusion matrix
confusionMatrix = confusion_matrix(test_label, test_pred)
f, ax = plt.subplots(figsize = (10,10))
sns.heatmap(confusionMatrix, annot = True, linewidths = 0.1, cmap = "gist_yarg_r", linecolor = "black", fmt = '.0f', ax = ax)
plt.xlabel("Predicted Label")
plt.ylabel("True Label")
plt.title("Confusion Matrix")
plt.show()


# # MODEL 2

# In[ ]:


import numpy as np
import pandas as pd
import random
import matplotlib.pyplot as plt
from statistics import mode
from statistics import median 
import tensorflow
from tensorflow.keras import datasets, layers, models
from tensorflow.keras import Sequential
from tensorflow.keras.layers import Conv2D, MaxPooling2D, Dense, Flatten, Dropout


##import the data
train = np.load("train_data_label.npz")
test = np.load("test_data_label.npz")

list(train.keys())

train_data = train['train_data']

train_label = train['train_label']
print(train_label)

test_data = test['test_data']
test_label = test['test_label']

print(train_data.shape)
print(test_data.shape)
print(train_label.shape)
print(test_label.shape)

#Specifying class labels
class_names = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J','K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y' ]

##check a random image from the training set to verify its class label.

#See a random image for class label verification
i = random.randint(1,train_data.shape[0])
plt.imshow(train_data[i,:].reshape((28,28))) 
label_index = train_label[i]
plt.title(f"{class_names[label_index]}")
plt.axis('off')

#plot some random images from the training set with their class labels.

# Define the dimensions of the plot grid 
W_grid = 5
L_grid = 5
fig, axes = plt.subplots(L_grid, W_grid, figsize = (10,10))
axes = axes.ravel() # flaten the 15 x 15 matrix into 225 array
n_train = len(train_data) # get the length of the train dataset
# Select a random number from 0 to n_train
for i in np.arange(0, W_grid * L_grid): # create evenly spaces variables 
    # Select a random number
    index = np.random.randint(0, n_train)
    # read and display an image with the selected index    
    axes[i].imshow( train_data[index,:].reshape((28,28)) )
    label_index = int(train_label[index])
    axes[i].set_title(class_names[label_index], fontsize = 8)
    axes[i].axis('off')
plt.subplots_adjust(hspace=0.4)

# Prepare the training and testing dataset
x_train = train_data
x_test = test_data
y_test = test_label

##Label Encoding. Turn the classes into one-hot encoding label.
from keras.utils.np_utils import to_categorical
y_train = to_categorical(train_label, num_classes=25)

#Split the training and validation sets
from sklearn.model_selection import train_test_split
x_train, x_validate, y_train, y_validate = train_test_split(x_train, y_train, test_size = 0.2, random_state = 12345)

##To train the model, we will unfold the data to make it available for training, testing and validation purposes.

# reshape the x values
x_train = x_train.reshape(x_train.shape[0], *(28, 28, 1))
x_test = x_test.reshape(x_test.shape[0], *(28, 28, 1))
x_validate = x_validate.reshape(x_validate.shape[0], *(28, 28, 1))

print(x_train.shape)
print(x_test.shape)
print(y_train.shape)
print(x_validate.shape)
print(y_validate.shape)

#Defining the Convolutional Neural Network
model1 = Sequential()

model1.add(Conv2D(filters = 32, kernel_size = (3,3), padding = 'Same', activation='relu', input_shape=(28,28,1)))
model1.add(MaxPooling2D(pool_size = (2,2)))
model1.add(Dropout(0.25))

model1.add(Flatten())

model1.add(Dense(units = 25, activation = 'softmax'))

model1.compile(loss ='categorical_crossentropy', optimizer='adam', metrics =['acc'])
model1.summary()

#Training the CNN model
history = model1.fit(x_train, y_train, batch_size = 128, epochs = 8, verbose = 1, validation_data = (x_validate, y_validate))

##plot the training and validation accuracy and loss at each epoch.It seems overfitting.
loss = history.history['loss']
val_loss = history.history['val_loss']
epochs = range(1, len(loss) + 1)
plt.plot(epochs, loss, 'y', label='Training loss')
plt.plot(epochs, val_loss, 'r', label='Validation loss')
plt.title('Training and validation loss')
plt.xlabel('Epochs')
plt.ylabel('Loss')
plt.legend()
plt.show()

acc = history.history['acc']
val_acc = history.history['val_acc']

plt.plot(epochs, acc, 'y', label='Training acc')
plt.plot(epochs, val_acc, 'r', label='Validation acc')
plt.title('Training and validation accuracy')
plt.xlabel('Epochs')
plt.ylabel('Accuracy')
plt.legend()
plt.show()

y_test = to_categorical(test_label, num_classes=25)# One-Hot Encoding
score = model1.evaluate(x_test,y_test, verbose =0)
print("Test Loss: {:.4f}".format(score[0]))
print("Test Accuracy: {:.2f}%".format(score[1]*100))

##predict test data
y_pred = model1.predict(x_test)
y_pred_classes = np.argmax(y_pred, axis = 1)
y_true = np.argmax(y_test, axis = 1)

print(y_pred)
print(y_pred_classes)
print(y_true)

#Classification accuracy
from sklearn.metrics import accuracy_score
acc_score = accuracy_score(y_true, y_pred_classes)
print('Accuracy Score = ',acc_score)

from sklearn.metrics import confusion_matrix
import seaborn as sns

confusionMatrix = confusion_matrix(y_true, y_pred_classes)
f,ax=plt.subplots(figsize=(10,10))
sns.heatmap(confusionMatrix, annot=True, linewidths=0.1, cmap = "gist_yarg_r", linecolor="black", fmt='.0f', ax=ax)
plt.xlabel("Predicted Label")
plt.ylabel("True Label")
plt.title("Confusion Matrix")
plt.show()


# # TASK 2

# In[ ]:


##load the data
data = np.load('test_images_task2.npy')

##check the data shape. it is a 3d dataset, has 10,000 elements.
print(data.shape)

##check the data shape of every element
print(data[1].shape)

##visual inspection of the images
def show_image(n):
    fig = plt.imshow(data[n].reshape(28, 200), aspect = 'equal')
    print(n)
    
show_image(103)

##check What is the data like for each sample, and we found out that most of the numbers are 200. 
##so 200 might be the grey part of the picture. The images we want to predict might be right after the grey part.
    
eg = data[103]
for n,pix in enumerate(eg):
   
    print( pix)

#Strategy to detect the images: 
#1: find the mode number of each column.If it is 200, we moved to the next column. If the mode number is not 200, we extracted 28 columns to predict the first picture.Continue the loop until we went through all the columns.
#2: find the median number of each column（200）.
#3: find the mean of the column which is less than 150. because we checked the mean of the columns of the samples, most of them is less than 150
#   because Neural network gives different output for same input. so we will predict method 1 for 2 times, method2 for 2 times, method3 for one time.

##check a random element to predict the first picture
mode(eg[:,1]) == 200

mode(eg[:,2]) == 200

plt.imshow(eg[:, 2:30].reshape((28,28)))


## method 1: mode
def image_extractor_mode(eg):
    
    i = 0
    output_list = []
    
    while i < data.shape[2]:
        
        col = eg[:,i]
        
        if mode(col) == 200:
            i += 1
            continue
        
        else:
            ims = eg[:,i:i+28]
            predictions =model1.predict(ims.reshape(1, 28, 28, 1))
            label = np.argmax(predictions, axis = 1)
            output_list.append(int(label))
            i = i + 28
            continue
    return output_list

##test the first two samples
print(image_extractor_mode(data[0]))
print(image_extractor_mode(data[1]))


##method 2: median
def image_extractor_median(eg):
    
    i = 0
    output_list = []
    
    while i < data.shape[2]:
        
        col = eg[:,i]
        
        if median(col) == 200:
            i += 1
            continue
        
        elif (200 - i) > 28:
            ims = eg[:,i:i+28]
            predictions =model1.predict(ims.reshape(1, 28, 28, 1))
            label = np.argmax(predictions, axis = 1)
            output_list.append(int(label))
            i = i + 28
            continue
        else:
            i += 1
            continue
    return output_list

##test the first two samples
print(image_extractor_median(data[0]))
print(image_extractor_median(data[1]))


##method 3:
def image_extractor_mean(eg):
    
    i = 0
    output_list = []
    
    while i < data.shape[2]:
        
        col = eg[:,i]
        
        if np.mean(col) > 150:
            i += 1
            continue
        
        elif (200 - i) > 28:
            ims = eg[:,i:i+28]
            predictions =model1.predict(ims.reshape(1, 28, 28, 1))
            label = np.argmax(predictions, axis = 1)
            output_list.append(int(label))
            i = i + 28
            continue
        else:
            i += 1
            continue
    return output_list

print(image_extractor_mean(data[0]))
print(image_extractor_mean(data[1]))


# predict the data set
##mode for 2 times
def result_predict_mode(data):
    final = []
    for image in data[:]:
        alltogether = ''.join(f'{x:02d}' for x in image_extractor_mode(image))
        final.append(alltogether)
    return final

# return the first two predictions
final_1 = result_predict_mode(data)
final_2 = result_predict_mode(data)

print(final_1[:5])
print(final_2[:5])

##median for 2 times
def result_predict_median(data):
    final = []
    for image in data[:]:
        alltogether = ''.join(f'{x:02d}' for x in image_extractor_median(image))
        final.append(alltogether)
    return final

# return the predictions
final_3 = result_predict_median(data)
final_4 = result_predict_median(data)

print(final_3[:5])
print(final_4[:5])

##mean for 1 time
def result_predict_mean(data):
    final = []
    for image in data[:]:
        alltogether = ''.join(f'{x:02d}' for x in image_extractor_mean(image))
        final.append(alltogether)
    return final

# run the prediction
final_5 = result_predict_mean(data)
print(final_5[:5])


#convert into a list of strings so excel doesn't remove leading zeros
#strings_final = [str(x) for x in final]

# make it into a pandas dataframe

df = pd.DataFrame(list(map(list, zip(final_1, final_2, final_3, final_4, final_5))))
print(df.head())

#Get it into csv file

import csv

df.to_csv('prediction.csv', index=False, header=False)

