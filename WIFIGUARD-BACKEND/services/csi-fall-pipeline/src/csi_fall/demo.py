"""Generate synthetic raw local CSVs. This is a plumbing test, not fall evidence."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from .io import write_json


def generate(directory, feature='channel_mean', cwt=False):
    directory = Path(directory).resolve()
    if directory.exists() and any(directory.iterdir()):
        raise FileExistsError('Demo directory must be new or empty')
    directory.mkdir(parents=True, exist_ok=True)
    roles = [('source', 'train'), ('source', 'train'), ('source', 'val'), ('target', 'train'), ('target', 'train'), ('target', 'val'), ('target', 'test'), ('external', 'external')]
    records = []
    fs, n = 40., 480
    t = np.arange(n)/fs
    for i, (role, split) in enumerate(roles):
        rng = np.random.default_rng(700+i)
        amplitude = 30 + rng.normal(0, 1, (n, 245)) + 3*np.sin(2*np.pi*.5*t[:, None]+rng.uniform(0, 2*np.pi, (1, 245)))
        event = (t >= 4) & (t < 5)
        amplitude += (18*np.exp(-((t-4.5)/.16)**2))[:, None]*rng.uniform(.5, 1.5, (1, 245))
        pairs = np.stack([np.zeros_like(amplitude), amplitude], axis=-1).round().astype(int).reshape(n, 490)
        filename = f'synthetic_{i}.csv'
        pd.DataFrame(dict(dev_timestamp=np.round(t*1e6).astype(np.int64), csi=[json.dumps(row.tolist()) for row in pairs], label=np.where(event, 'a10', 'none'))).to_csv(directory/filename, index=False)
        records.append(dict(id=f'synthetic_{i}', format='local', path=filename, fs=fs, role=role, split=split))
    cfg = dict(output='outputs/demo', feature=feature, cwt=cwt, backbone='compact', window_seconds=3, stride_seconds=1, overlap_threshold=.5, guard_seconds=.5, training=dict(seed=42, source_epochs=1, target_epochs=1, batch_size=8, refit=True), records=records)
    write_json(directory/'config.json', cfg)
    write_json(directory/'predict_record.json', records[-1])
    print(f'Synthetic demo generated: {directory / "config.json"}', flush=True)
