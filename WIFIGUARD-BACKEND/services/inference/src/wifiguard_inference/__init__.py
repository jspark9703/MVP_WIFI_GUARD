"""WIFI-GUARD live model inference service."""

from .settings import InferenceSettings
from .worker import InferenceWorker

__all__ = ["InferenceSettings", "InferenceWorker"]
