import pytest
import numpy as np
import pandas as pd
from engine.model_loader import get_model_container
from engine.feature_pipeline import get_feature_pipeline

def test_model_loading():
    container = get_model_container()
    success = container.load_models()
    assert success is True
    assert container.is_ready is True
    assert len(container.models) >= 1

def test_feature_pipeline_alignment():
    pipeline = get_feature_pipeline()
    raw = {
        "TransactionAmt": 150.0,
        "ProductCD": "W",
        "card1": 1040,
        "card4": "visa",
        "P_emaildomain": "gmail.com",
    }
    df = pipeline.prepare_dataframe(raw)
    assert df.shape[0] == 1
    assert df.shape[1] == len(pipeline.feature_names)
    assert "TransactionAmt_to_mean_card1" in df.columns
    assert "TransactionAmt_to_mean_card4" in df.columns

def test_predict_fast_mode():
    container = get_model_container()
    pipeline = get_feature_pipeline()
    raw = {"TransactionAmt": 50.0, "card4": "visa"}
    df = pipeline.prepare_dataframe(raw)
    probs, latency_ms = container.predict(df, mode="fast")

    assert len(probs) == 1
    assert 0.0 <= probs[0] <= 1.0
    assert latency_ms > 0

def test_predict_ensemble_mode():
    container = get_model_container()
    pipeline = get_feature_pipeline()
    raw = {"TransactionAmt": 50.0, "card4": "visa"}
    df = pipeline.prepare_dataframe(raw)
    probs, latency_ms = container.predict(df, mode="ensemble")

    assert len(probs) == 1
    assert 0.0 <= probs[0] <= 1.0

