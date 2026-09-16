"""A single feature implementation shared by preparation and raw prediction."""
import numpy as np
import torch
from . import legacy
from .multichannel import KINDS, channel_products, filter_window
from .scalars import extract as scalar_extract

CHOICES = [*KINDS, 'pca_map', 'legacy_map', 'curve', 'scalars']


@torch.no_grad()
def extract(windows, fs, kind='channel_mean', cwt=False, device='cpu'):
    windows = np.asarray(windows, dtype=np.float32)
    if windows.ndim != 3 or windows.shape[-1] != 30 or not np.isfinite(windows).all():
        raise ValueError('Expected finite batch × time × 30 amplitude')
    if kind not in CHOICES or not np.isfinite(fs) or fs <= 0 or windows.shape[1] <= np.ceil(.4*fs):
        raise ValueError('Unknown feature or insufficient samples for lag 0–0.4s')
    if kind in KINDS:
        x = torch.as_tensor(windows, device=device)
        if kind in ('channel_mean', 'channel_energy', 'channel_acf'):
            mean, energy, curve, _ = channel_products(x, fs)
            value = {'channel_mean': mean, 'channel_energy': energy, 'channel_acf': curve}[kind]
        else:
            y = x.mean(-1, keepdim=True) if kind == 'mean_signal' else filter_window(x, fs, kind)
            value = channel_products(y, fs, extras=False)[0]
        secondary = value.cpu().numpy()
    signals = []
    if cwt or kind not in KINDS:
        radius = max(1, round(.4*fs))
        for window in windows:
            selected = legacy.select_streams(window, 30, radius)
            signals.append(legacy.select_pc_signal(window, selected, radius))
    if kind == 'legacy_map':
        secondary = np.stack([legacy.compute_acf(s, fs, legacy.DEFAULT_CONFIG) for s in signals])
    elif kind == 'pca_map':
        x = torch.as_tensor(np.stack(signals)[:, :, None], device=device)
        secondary = channel_products(x, fs, extras=False)[0].cpu().numpy()
    elif kind in ('curve', 'scalars'):
        pairs = [scalar_extract(s, fs) for s in signals]
        secondary = np.stack([v[0 if kind == 'curve' else 1] for v in pairs])
        if kind == 'curve':
            secondary = np.nan_to_num(secondary)[:, None, :]
    # Same storage precision in extraction and prediction, avoiding train/infer drift.
    secondary = secondary.astype(np.float32 if kind == 'scalars' else np.float16)
    if kind != 'scalars' and not np.isfinite(secondary).all():
        raise ValueError('Nonfinite or float16-overflowed ACF feature')
    result = {'secondary': secondary}
    if cwt:
        result['cwt'] = np.stack([legacy.compute_s3(s, fs, legacy.DEFAULT_CONFIG) for s in signals])[:, None].astype(np.float16)
        if not np.isfinite(result['cwt']).all():
            raise ValueError('Nonfinite CWT feature')
    return result
