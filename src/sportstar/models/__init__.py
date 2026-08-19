"""Modelos y su interfaz común."""

from .base import ModelPrediction, SportModel
from .market_consensus import MODEL_NAME, MODEL_VERSION, MarketConsensusModel

__all__ = ["MODEL_NAME", "MODEL_VERSION", "MarketConsensusModel", "ModelPrediction", "SportModel"]
