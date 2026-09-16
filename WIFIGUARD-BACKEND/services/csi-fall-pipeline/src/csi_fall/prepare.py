"""Build recording shards from raw CSV/NPZ with provenance and split guards."""
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from .features import extract, CHOICES
from .io import load_record, native_window_starts, window_labels, validate_records, write_json, file_hash, resolve


def configuration(path):
    path = Path(path).resolve()
    cfg = json.loads(path.read_text())
    cfg.setdefault('feature', 'channel_mean')
    cfg.setdefault('cwt', False)
    cfg.setdefault('window_seconds', 3.)
    cfg.setdefault('stride_seconds', .25)
    cfg.setdefault('overlap_threshold', .5)
    cfg.setdefault('guard_seconds', .5)
    cfg.setdefault('backbone', 'compact')
    if cfg['feature'] not in CHOICES or cfg['window_seconds'] != 3 or cfg['stride_seconds'] <= 0:
        raise ValueError('Select a supported feature, 3-second window, and positive stride')
    out = resolve(path.parent, cfg.get('output', 'outputs/experiment'))
    return cfg, path.parent, out


def prepare(path, device='cpu', batch_size=16):
    if batch_size < 1:
        raise ValueError('batch_size must be positive')
    cfg, base, out = configuration(path)
    sources = validate_records(cfg['records'], base)
    hashes = {str(p): file_hash(p) for p in sources}
    canonical = json.dumps({'config': cfg, 'raw_sha256': hashes}, sort_keys=True)
    signature = hashlib.sha256(canonical.encode()).hexdigest()
    cache = out/'cache'
    meta = cache/'metadata.json'
    if meta.exists():
        old = json.loads(meta.read_text())
        if old['signature'] != signature:
            raise ValueError('Output belongs to different inputs/configuration. Select a new output directory.')
        if (cache/'manifest.csv').is_file():
            frame = pd.read_csv(cache/'manifest.csv')
            if all((cache/p).is_file() or ((cache/p/'secondary.npy').is_file() and (not cfg['cwt'] or (cache/p/'cwt.npy').is_file())) for p in frame.shard.unique()):
                print(f'Reusing completed cache: {cache}', flush=True)
                return out
            raise ValueError('Completed cache has missing shards; use a new output directory')
    cache.mkdir(parents=True, exist_ok=True)
    # A preparation marker prevents mixing a partial cache with different raw inputs.
    marker = cache/'preparing.json'
    if marker.exists() and json.loads(marker.read_text())['signature'] != signature:
        raise ValueError('Partial cache belongs to another configuration')
    write_json(marker, {'signature': signature})
    rows, audits = [], []
    for record in cfg['records']:
        amp, labels, fs, audit = load_record(record, base)
        if labels is None:
            raise ValueError(f"{record['id']}: labels are required for feature preparation; use predict for unlabeled recordings")
        starts = native_window_starts(len(amp), fs, 3, cfg['stride_seconds'])
        n = round(3*fs)
        keep, y = window_labels(starts, n, labels, fs, cfg['overlap_threshold'], cfg['guard_seconds'])
        selected = starts[keep]
        if not len(selected):
            raise ValueError(f"{record['id']}: no labeled full windows")
        audit.update(id=record['id'], kept_windows_per_rx=len(selected), ambiguous_windows=len(starts)-len(selected))
        audits.append(audit)
        for rx in range(amp.shape[1]//30):
            key = f"{record['id']}_rx{rx+1}"
            shard = key
            blocks = {}
            for begin in range(0, len(selected), batch_size):
                batch = np.stack([amp[s:s+n, rx*30:(rx+1)*30] for s in selected[begin:begin+batch_size]])
                values = extract(batch, fs, cfg['feature'], cfg['cwt'], device)
                for name, value in values.items():
                    blocks.setdefault(name, []).append(value)
            data = {name: np.concatenate(parts) for name, parts in blocks.items()}
            (cache/shard).mkdir(exist_ok=True)
            for name, value in data.items():
                temp = cache/shard/(name+'.tmp.npy')
                np.save(temp, value)
                temp.replace(cache/shard/(name+'.npy'))
            for j, (start, label) in enumerate(zip(selected, y)):
                rows.append(dict(sample_id=f'{key}_s{start}', recording_id=record['id'], group=record.get('group', record['id']), role=record['role'], split=record['split'], rx=rx+1, window_start=int(start), fs=fs, label=int(label), shard=shard, row=j))
        print(f"Prepared {record['id']}: {len(selected)} windows/RX", flush=True)
    frame = pd.DataFrame(rows)
    if not frame.sample_id.is_unique:
        raise ValueError('Duplicate sample IDs')
    frame.to_csv(cache/'manifest.csv', index=False)
    write_json(meta, dict(version=1, signature=signature, config=cfg, raw_sha256=hashes, records=audits, windows=len(frame)))
    marker.unlink(missing_ok=True)
    return out
