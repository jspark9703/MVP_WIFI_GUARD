"""The single live feature adapter shared with the Raspberry Pi package."""

from __future__ import annotations

import numpy as np


class LiveFeatureBuilder:
    """Convert the edge's 1-D representative signal into model tensors.

    Importing the implementation from ``wifiguard-edge`` avoids a silent train/live
    preprocessing fork. ``start()`` checks that the exact ssqueezepy path is present.
    """

    def __init__(self) -> None:
        from wifiguard_edge.features.realtime import FeatureConfig

        self.config = FeatureConfig()

    def start(self) -> None:
        from wifiguard_edge.features.realtime import require_exact_cwt

        require_exact_cwt()

    def build(self, signal: np.ndarray, fs_hz: float):
        from wifiguard_edge.features.realtime import features_from_signal

        return features_from_signal(signal, fs_hz, self.config)
