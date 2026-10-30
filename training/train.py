"""
PayShield Model Training Pipeline
----------------------------------
Converts the IEEE-CIS Fraud Detection dataset into the calibrated LightGBM
5-fold ensemble artifacts (lgb_models.pkl & result_dict_lgb.pkl).

Pipeline stages:
1. Data loading & merging (transaction + identity)
2. Feature engineering (amount ratios, D15 interactions, email domains)
3. Dimensionality & high-cardinality cleaning
4. 5-Fold Stratified Cross-Validation with LightGBM
5. Out-Of-Fold (OOF) prediction generation for threshold calibration
"""

import os
import time
import pickle
import warnings
from typing import Optional
import numpy as np
import pandas as pd
from sklearn.model_selection import StratifiedKFold
from sklearn.preprocessing import LabelEncoder
from sklearn import metrics
import lightgbm as lgb

warnings.filterwarnings("ignore")

# Default Hyperparameters
LGB_PARAMS = {
    "num_leaves": 256,
    "min_child_samples": 79,
    "objective": "binary",
    "max_depth": 13,
    "learning_rate": 0.03,
    "boosting_type": "gbdt",
    "subsample_freq": 3,
    "subsample": 0.9,
    "bagging_seed": 11,
    "metric": "auc",
    "verbosity": -1,
    "reg_alpha": 0.3,
    "reg_lambda": 0.3,
    "colsample_bytree": 0.9,
    "n_estimators": 5000,
    "n_jobs": -1,
}

def engineer_features(train: pd.DataFrame, test: Optional[pd.DataFrame] = None):
    """
    Constructs domain-specific interaction features:
    - Amount deviations relative to card network / card identifier
    - Identity consistency ratios
    - Timedelta baseline normalizations (D15)
    - Email domain components
    """
    print("Executing feature engineering pipeline...")
    dfs = [train] if test is None else [train, test]

    for df in dfs:
        # 1. TransactionAmt interactions
        df["TransactionAmt_to_mean_card1"] = df["TransactionAmt"] / df.groupby(["card1"])["TransactionAmt"].transform("mean")
        df["TransactionAmt_to_mean_card4"] = df["TransactionAmt"] / df.groupby(["card4"])["TransactionAmt"].transform("mean")
        df["TransactionAmt_to_std_card1"] = df["TransactionAmt"] / df.groupby(["card1"])["TransactionAmt"].transform("std")
        df["TransactionAmt_to_std_card4"] = df["TransactionAmt"] / df.groupby(["card4"])["TransactionAmt"].transform("std")

        # 2. id_02 interactions
        if "id_02" in df.columns:
            df["id_02_to_mean_card1"] = df["id_02"] / df.groupby(["card1"])["id_02"].transform("mean")
            df["id_02_to_mean_card4"] = df["id_02"] / df.groupby(["card4"])["id_02"].transform("mean")
            df["id_02_to_std_card1"] = df["id_02"] / df.groupby(["card1"])["id_02"].transform("std")
            df["id_02_to_std_card4"] = df["id_02"] / df.groupby(["card4"])["id_02"].transform("std")

        # 3. D15 interactions
        if "D15" in df.columns:
            df["D15_to_mean_card1"] = df["D15"] / df.groupby(["card1"])["D15"].transform("mean")
            df["D15_to_mean_card4"] = df["D15"] / df.groupby(["card4"])["D15"].transform("mean")
            df["D15_to_std_card1"] = df["D15"] / df.groupby(["card1"])["D15"].transform("std")
            df["D15_to_std_card4"] = df["D15"] / df.groupby(["card4"])["D15"].transform("std")
            df["D15_to_mean_addr1"] = df["D15"] / df.groupby(["addr1"])["D15"].transform("mean")
            df["D15_to_mean_addr2"] = df["D15"] / df.groupby(["addr2"])["D15"].transform("mean")
            df["D15_to_std_addr1"] = df["D15"] / df.groupby(["addr1"])["D15"].transform("std")
            df["D15_to_std_addr2"] = df["D15"] / df.groupby(["addr2"])["D15"].transform("std")

        # 4. Email domain splitting
        if "P_emaildomain" in df.columns:
            df[["P_emaildomain_1", "P_emaildomain_2", "P_emaildomain_3"]] = df["P_emaildomain"].str.split(".", expand=True)
        if "R_emaildomain" in df.columns:
            df[["R_emaildomain_1", "R_emaildomain_2", "R_emaildomain_3"]] = df["R_emaildomain"].str.split(".", expand=True)

    return train, test

def train_kfold_lightgbm(X: pd.DataFrame, y: pd.Series, params: dict, n_splits: int = 5):
    """
    Trains 5-fold LightGBM classifier and collects out-of-fold predictions.
    """
    folds = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
    models = []
    oof = np.zeros(len(X))
    scores = []

    callbacks = [
        lgb.early_stopping(stopping_rounds=200, verbose=False),
        lgb.log_evaluation(period=500)
    ]

    for fold_n, (trn_idx, val_idx) in enumerate(folds.split(X, y)):
        print(f"\n--- Training Fold {fold_n + 1}/{n_splits} ---")
        X_train, y_train = X.iloc[trn_idx], y.iloc[trn_idx]
        X_valid, y_valid = X.iloc[val_idx], y.iloc[val_idx]

        model = lgb.LGBMClassifier(**params)
        model.fit(
            X_train, y_train,
            eval_set=[(X_train, y_train), (X_valid, y_valid)],
            callbacks=callbacks
        )

        val_preds = model.predict_proba(X_valid)[:, 1]
        oof[val_idx] = val_preds
        auc_score = metrics.roc_auc_score(y_valid, val_preds)
        scores.append(auc_score)
        models.append(model)
        print(f"Fold {fold_n + 1} ROC-AUC: {auc_score:.5f}")

    mean_auc = np.mean(scores)
    print(f"\nOverall Cross-Validation ROC-AUC: {mean_auc:.5f}")
    return models, oof, scores

if __name__ == "__main__":
    print("PayShield Training Module ready.")
    print("To execute full training, provide raw train_transaction.csv and train_identity.csv")

