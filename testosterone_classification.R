# 🎵 CLEAN UP CLEAN UP 🎶
#setwd!

#INSTALL PACKAGES
install.packages("tidyverse")
library(tidyverse)
library(haven)

#LOAD IN ALL DATA SETS
demo = read_xpt('DEMO_I.XPT')
bmidata = read_xpt('BMX_I.XPT')
question = read_xpt('MCQ_I.XPT')
test = read_xpt('TST_I.XPT')

intakedy1 = read_xpt('DR1TOT_I.XPT')
intakedy2 = read_xpt('DR2TOT_I.XPT')
supdy1 = read_xpt('DS1TOT_I.XPT')
supdy2 = read_xpt('DS2TOT_I.XPT')
sup30dy = read_xpt('DSQTOT_I.XPT')

#DELETING INSTANCES WITHOUT TESTOSTERONE VALUES
noNAstest <- test %>% drop_na(LBXTST)

#CREATING A VARIABLE FOR TEST DEFICIENCY
noNAstest$testdef <- noNAstest$LBXTST <= 350

#CONVERT INTAKES INTO ZEROS FOR THE CALCULATIONS IN FEATURE ENGINEERING
  supslist <- list(supdy1,supdy2,sup30dy)  
  sups <- supslist %>% reduce(full_join, by = 'SEQN')
  sups <- mutate_all(sups, ~replace_na(.,0))
  food <- inner_join(intakedy1,intakedy2, by = 'SEQN')
intakestable <- inner_join(food, sups,by = 'SEQN')
intakestable <- intakestable[, c(grep("^DR1T", names(intakestable)), which(names(intakestable) == "SEQN"), grep("^DR2T", names(intakestable)),grep("^DS1T", names(intakestable)), grep("^DS2T", names(intakestable)),grep("^DSQT", names(intakestable))) ]
#NAzerointakes <- mutate_all(intakestable, ~replace_na(.,0))

#COMBINE ALL DATASETS
list_dfs <- list(demo, noNAstest, question)  
dfcombined <- list_dfs %>% reduce(inner_join, by = 'SEQN')

#EXCLUDE EVERYONE WHO IS NOT A MALE
males = dfcombined[dfcombined$RIAGENDR == 1,]

#EXCLUDE EVERYONE IS LESS THAN 20
adults = subset(males, DMDHRAGE>=20)

#INCLUDE ONLY THOSE WHO DO NOT HAVE CORONARY HEART DISEASE
noCHD = subset(adults,MCQ160C== 2 & !is.na(MCQ160C))

#EXCLUDE THOSE WITH PROSTATE CANCER
nopc = subset(noCHD,MCQ230A != 30 | is.na(MCQ230A) | MCQ230B != 30 | is.na(MCQ230B) | MCQ230C != 30 | is.na(MCQ230C) | MCQ230D != 30 | is.na(MCQ230D))

#SELECTING CONFOUNDER VARIABLES AND TARGET VARIABLE
confoundervars <- nopc %>% dplyr::select(c(SEQN,testdef,LBXTST))

data <- inner_join(na.omit(confoundervars), intakestable, by = 'SEQN')

#Delete columns with more than 20% missing values
# identify columns with less than 75% non-missing values
more_than_25pct_cols <- which(colMeans(is.na(data)) < 0.25)

# remove columns with more than 25 percent missing values
data <- data[, more_than_25pct_cols]

#RENAMING COLUMNS
data <- data %>% rename(c("ID" = "SEQN", "TDS" = "testdef", "TEST" = "LBXTST"))

#REMOVING THE ID COLUMN
data <- data %>% dplyr::select(-c(ID,DS1TCAFF,DS2TCAFF))

#Identify non-numeric variables except TDS
non_numeric_vars <- names(data)[!sapply(data, is.numeric) & names(data) != "TDS"]

#Remove non-numeric variables except TDS
data <- data[, !(names(data) %in% non_numeric_vars)]

#RELOCATE TDS TO THE LAST COLUMN FOR VISIBILITY
data <- relocate(data, TDS, .after = last_col())

#PREPROCESSING
install.packages("mltools")
library(mltools)
library(data.table)
data$TDS <- as.factor(data$TDS)


#SPLIT DATA TRAIN-TEST
set.seed(123)  # for reproducibility
library(caret)
trainIndex <- createDataPartition(data$TDS, p = 0.8, list = FALSE)
train_set <- data[trainIndex,]
test_set <- data[-trainIndex,]

#Deletion of Outliers
#train_set<- train_set[!train_set %in% boxplot(train_set)$out]  

#Standardize the data
train_set <- train_set %>% mutate_if(is.numeric, scale)
test_set <- test_set %>% mutate_if(is.numeric, scale)

#KNN IMPUTATION
install.packages("VIM")
library(VIM)
set.seed(123)
imputed_train <- kNN(train_set, k = 5,impNA = TRUE, imp_var = FALSE)
imputed_test <- kNN(test_set, k = 5, impNA = TRUE, imp_var = FALSE)

#UNDERSAMPLING
balanced_train <- downSample(x = imputed_train[,-ncol(imputed_train)], y = imputed_train$TDS, list = FALSE, yname = "TDS")
balanced_train <- dplyr::select(balanced_train, -TEST)
imputed_test <- dplyr::select(imputed_test, -TEST)
#shuffle the data 
set.seed(123)
balanced_train <- balanced_train[sample(nrow(balanced_train)),]
imputed_test <- imputed_test[sample(nrow(imputed_test)),]

# EXPLORING NEW TERRITORIES 🚀
head(balanced_train)
names(balanced_train)
dim(balanced_train)
str(balanced_train)
summary(balanced_train)
colSums(is.na(balanced_train))
balanced_train[balanced_train$TEST>1200,]

#EXPERIMENT
#Cross Validation + recursive feature elimination
x_train <- balanced_train[,-ncol(balanced_train)]
y_train <- as.factor(balanced_train[,ncol(balanced_train)])
x_test <- imputed_test[,-ncol(imputed_test)]
y_test <- as.factor(imputed_test[,ncol(imputed_test)])

# Automatic feature selection using Recurrent Feature Elimination
install.packages("randomForest")
library(randomForest)
set.seed(123)
control <- rfeControl(functions = rfFuncs, # random forest
                      method = "cv", # cv
                      number = 5) # number of folds
result_rfe <- rfe(x = x_train, 
                  y = y_train, 
                  sizes = c(1:30),
                  metric = "Accuracy",
                  maximize = TRUE,
                  rfeControl = control)

# Print the results
result_rfe

# Print all features in order of importance
predictors(result_rfe)
result_rfe$optsize
#was just curious about how many variables to add 
pickSizeTolerance(result_rfe$results, metric = "Accuracy",maximize = TRUE, tol = 1)

#Made a function to exclude multicollinearity >= 0.3 out of all of the variables 
#The below function was written by Chatgpt
# Select the variables you want to check for correlation
selected_vars <- result_rfe$variables$var[1:ncol(x_train)]

# Define correlation threshold
cor_threshold <- 0.3

# Create an empty vector to store the selected features
selected_features <- c()
# Loop through each feature in order of importance
for (feature in selected_vars) {
  # Check if the feature is correlated with any previously selected features
  if (all(abs(cor(x_train[, selected_features], x_train[, feature])) < cor_threshold)) {
    # If not correlated, add the feature to the selected features
    selected_features <- c(selected_features, feature) 
  }
  # Exit the loop when the desired number of features have been selected
    if (length(selected_features) == result_rfe$optsize) {
      break
    }
  }

# Print the selected features (of the best features, those without multi-collinearity)
paste0(selected_features, collapse = "+")

# Print the results visually
ggplot(data = result_rfe, metric = "Accuracy") + theme_bw()
ggplot(data = result_rfe, metric = "Kappa") + theme_bw()

varimp_data <- data.frame(feature = row.names(varImp(result_rfe))[1:result_rfe$optsize],
                          importance = varImp(result_rfe)[1:result_rfe$optsize, 1])
print(varimp_data)
ggplot(data = varimp_data, 
       aes(x = reorder(feature, -importance), y = importance, fill = feature)) +
  geom_bar(stat="identity") + labs(x = "Features", y = "Variable Importance") + 
  geom_text(aes(label = round(importance, 2)), vjust=1.6, color="white", size=3) + 
  theme_bw() + theme(legend.position = "none", axis.text.x = element_text(face="bold", color="black", 
                                                                          size=12, angle=90)) 
#overall accuracy and kappa values
postResample(predict(result_rfe, x_test), y_test)


#Logistic Regression
ctrlspec_logreg <- trainControl(method = "cv", number = 50, savePredictions = "all")
logreg_manual <- train(TDS ~ DR1TVARA + DR2TVARA + DR1TPROT+DR2TPROT+DS1TPROT+DS2TPROT+DSQTPROT+DR1TSFAT+DR2TSFAT+DS1TSFAT+DS2TSFAT+DSQTSFAT+ DR1TMFAT+DR2TMFAT+DS1TMFAT+DS2TMFAT+DSQTMFAT+ DR1TTFAT+DR2TTFAT+DS1TTFAT+DS2TTFAT+DSQTTFAT+ DR1TS160+DR2TS160+DR1TPHOS+DR2TPHOS+ DS1TPHOS+DS2TPHOS+DSQTPHOS, data = balanced_train, method = "glm", family = "binomial", trControl = ctrlspec_logreg)
logreg_auto <- train(TDS ~ DR2TS060+DR1TNIAC+DR1TFIBE+DR2TLZ+DSQTPOTA+DR2TTHEO+DR2TP226+DSQTIRON+DSQTVC+DR1TP205+DR1TLYCO+DSQTPROT+DSQTCALC+DS2TPROT+DSQTFA+DR2TCRYP+DS2TLZ+DR2TCAFF, data = balanced_train, method = 'glm', family = 'binomial', trControl = ctrlspec_logreg)
mod
#RUN XGBOOST
install.packages("xgboost") 
library(xgboost) 

xgboost_manual <- train(TDS ~ DR1TVARA + DR2TVARA + DR1TPROT+DR2TPROT+DS1TPROT+DS2TPROT+DSQTPROT+DR1TSFAT+DR2TSFAT+DS1TSFAT+DS2TSFAT+DSQTSFAT+ DR1TMFAT+DR2TMFAT+DS1TMFAT+DS2TMFAT+DSQTMFAT+ DR1TTFAT+DR2TTFAT+DS1TTFAT+DS2TTFAT+DSQTTFAT+ DR1TS160+DR2TS160+DR1TPHOS+DR2TPHOS+ DS1TPHOS+DS2TPHOS+DSQTPHOS, data = balanced_train, method = "xgbTree", trControl = ctrlspec_logreg)
xgboost_auto <- train(TDS ~ DR2TS060+DR1TNIAC+DR1TFIBE+DR2TLZ+DSQTPOTA+DR2TTHEO+DR2TP226+DSQTIRON+DSQTVC+DR1TP205+DR1TLYCO+DSQTPROT+DSQTCALC+DS2TPROT+DSQTFA+DR2TCRYP+DS2TLZ+DR2TCAFF, data = balanced_train, method = "xgbTree", trControl = ctrlspec_logreg)
xgboost_auto$results$Accuracy
feature_importance_manual <- varImp(xgboost_manual)
feature_importance_auto <- varImp(xgboost_auto)
print(feature_importance_manual)
print(feature_importance_auto)
coef_logmanual <- logreg_manual$finalModel$coefficients[order(abs(logreg_manual$finalModel$coefficients), decreasing = TRUE)]
table_manual <- data.frame(Variables = names(coef_logmanual), Coefficients = coef_logmanual)
table_manual[1:17,1:2]
coef_logauto <- logreg_auto$finalModel$coefficients[order(abs(logreg_auto$finalModel$coefficients), decreasing = TRUE)]
table_auto <- data.frame(Variables = names(coef_logauto), Coefficients = coef_logauto)
coef_logmanual <- data.frame(Variables_manual = table_manual[1:17,1], Coefficients_manual = table_manual[1:17,2], Variables_auto = table_auto[1:17,1], Coefficients_Auto = table_auto[1:27,2])
coef_logmanual
table_auto
table_manual


#PREDICT
logistic_pred_manual <- predict(logreg_manual, newdata = x_test)
logistic_pred_auto <- predict(logreg_auto, newdata = x_test)
xgboost_pred_manual <- predict(xgboost_manual, newdata = x_test)
xgboost_pred_auto <- predict(xgboost_auto, newdata = x_test)
#EVALUATION
install.packages("MLmetrics")
library(MLmetrics)

#confusion matrix
logistic_CM_manual <- ConfusionMatrix(logistic_pred_manual, y_test)
logistic_CM_auto <- ConfusionMatrix(logistic_pred_auto, y_test)
xgboost_CM_manual <- ConfusionMatrix(xgboost_pred_manual, y_test)
xgboost_CM_auto <- ConfusionMatrix(xgboost_pred_auto, y_test)

#accuracy score
logistic_accuracy_manual <- Accuracy(y_test, logistic_pred_manual)
logistic_accuracy_auto <- Accuracy(y_test, logistic_pred_auto)
xgboost_accuracy_manual <- Accuracy(y_test, xgboost_pred_manual)
xgboost_accuracy_auto <- Accuracy(y_test, xgboost_pred_auto)

#recall
logistic_recall_manual <- Recall(logistic_pred_manual,y_test)
logistic_recall_auto <- Recall(logistic_pred_auto,y_test)
xgboost_recall_manual <- Recall(xgboost_pred_manual, y_test)
xgboost_recall_auto <- Recall(xgboost_pred_auto, y_test)
#precision
logistic_precision_manual <- Precision(logistic_pred_manual, y_test)
logistic_precision_auto <- Precision(logistic_pred_auto, y_test)
xgboost_precision_manual <- Precision(xgboost_pred_manual, y_test)
xgboost_precision_auto <- Precision(xgboost_pred_auto, y_test)
#f1 score
logistic_F1_manual <- F1_Score(logistic_pred_manual, y_test)
logistic_F1_auto <- F1_Score(logistic_pred_auto, y_test)
xgboost_F1_manual <- F1_Score(xgboost_pred_manual, y_test)
xgboost_F1_auto <- F1_Score(xgboost_pred_auto, y_test)

#inspecting the data
install.packages("umap")
install.packages("plotly")
library(umap)
library(plotly)
balanced_train$RACE <- as.double(balanced_train$RACE)
balanced_train$EDUCATION <- as.double(balanced_train$EDUCATION)
balanced_train$PIR <- as.double(balanced_train$PIR)
balanced_train$TDS <- as.double(balanced_train$TDS)
balanced_train$DR1TWS <- as.double(balanced_train$DR1TWS)
balanced_train$DR2TWS <- as.double(balanced_train$DR2TWS)
map <- umap(balanced_train, n_components = 3)
map_plot <- ggplot() +
  geom_point(aes(x = map$layout[,1], y = map$layout[,2], z = map$layout[,3]))
print(map_plot)
#3d visual
plot_ly(x=map$layout[,1], y=map$layout[,2], z=map$layout[,3], type="scatter3d", mode="markers")

selected_features
