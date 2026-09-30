"""Compatibility imports for the active in-house segmentation feature path."""

from csi_fall_segmentation.online import (
    IncompatibleSignalError,
    SegmentationFeatureBuilder as LiveFeatureBuilder,
    SegmentationFeatures as LiveFeatures,
)

__all__ = ["IncompatibleSignalError", "LiveFeatureBuilder", "LiveFeatures"]
