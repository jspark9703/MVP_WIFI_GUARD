"""Portable raw adapters and recording-level split validation.

All intervals use [start, end). No old feature archive is required.
"""
import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
from .legacy import input_pair_indices, parse_csi_amplitude, native_window_starts


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    temp.replace(path)


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def resolve(base, path):
    p = Path(path).expanduser()
    return p.resolve() if p.is_absolute() else (Path(base) / p).resolve()


def nearest(source, target):
    right = np.searchsorted(source, target).clip(0, len(source)-1)
    left = np.maximum(right-1, 0)
    return np.where(abs(target-source[left]) <= abs(source[right]-target), left, right)


def unwrap_timestamp(ts):
    if not len(ts) or not np.isfinite(ts).all():
        raise ValueError('Timestamps must be nonempty and finite')
    out = np.empty_like(ts, dtype=float)
    offset = 0.
    for i, value in enumerate(ts):
        if i and ts[i-1]-value > 2**31:
            offset += 2**32
        out[i] = value+offset
    return out


def local_labels(values):
    labels = pd.Series(values).fillna('none').astype(str).str.strip().str.lower()
    allowed = {'none', '0', 'nonfall', 'non_fall', 'fall', '1'} | {f'a{i}' for i in range(1, 13)}
    unknown = set(labels)-allowed
    if unknown:
        raise ValueError(f'Unknown local labels: {sorted(unknown)}')
    return labels.isin({'a10', 'a11', 'a12', 'fall', '1'}).to_numpy(np.int8)


def load_record(record, base):
    """Return uniformly sampled amplitude, binary labels (or None), fs, audit.

    local: 245 imag/real pairs, chosen 30 after removing pairs 121/122.
    mendeley: ordered activity CSV segments with 3 RX × 30 subcarriers.
    amplitude: regular real amplitude .npz(amplitude, optional labels, fs).
    """
    fmt = record['format']
    fs = float(record.get('fs', 320 if fmt == 'mendeley' else 500/3))
    if not np.isfinite(fs) or fs <= 0:
        raise ValueError('fs must be finite and positive')
    labels = None
    audit = {'format': fmt}
    if fmt == 'amplitude':
        with np.load(resolve(base, record['path']), allow_pickle=False) as d:
            if np.iscomplexobj(d['amplitude']):
                raise ValueError('Amplitude NPZ must contain real magnitudes, not complex IQ')
            amplitude = d['amplitude'].astype(np.float32)
            fs = float(d['fs'])
            if 'labels' in d:
                raw_labels = d['labels']
                if not np.isin(raw_labels, [0, 1]).all():
                    raise ValueError('Amplitude NPZ labels must be binary 0/1')
                labels = raw_labels.astype(np.int8)
    elif fmt == 'local':
        d = pd.read_csv(resolve(base, record['path']))
        ts = unwrap_timestamp(d.dev_timestamp.to_numpy(float))
        order = np.argsort(ts, kind='stable')
        unique = np.r_[True, np.diff(ts[order]) > 0]
        positions = order[unique]
        ts = ts[positions]
        pairs = input_pair_indices(int(record.get('raw_pairs', 245)), record.get('drop_pairs', [121, 122]), 30)
        amplitude = np.stack([parse_csi_amplitude(x, pairs, int(record.get('raw_pairs', 245))) for x in d.csi.iloc[positions]])
        if len(ts) < 2:
            raise ValueError('At least two distinct timestamps required')
        grid = ts[0] + np.arange(int(np.floor((ts[-1]-ts[0])*fs/1e6))+1)*(1e6/fs)
        if 'label' in d:
            labels = local_labels(d.label.iloc[positions])[nearest(ts, grid)]
        amplitude = np.stack([np.interp(grid, ts, x) for x in amplitude.T], axis=1).astype(np.float32)
        audit.update(raw_rows=len(d), unique_rows=len(ts), max_gap_seconds=float(np.diff(ts).max()/1e6), selected_pairs=pairs.tolist())
    elif fmt == 'mendeley':
        ts_parts, amp_parts, label_parts = [], [], []
        segments = record['segments']
        if not segments:
            raise ValueError('Mendeley segments must be an ordered nonempty list')
        for segment in segments:
            d = pd.read_csv(resolve(base, segment['path']))
            activity = segment.get('activity')
            if activity is None:
                match = re.search(r'_(A\d{2})_', Path(segment['path']).name)
                if not match:
                    raise ValueError('Specify Mendeley activity explicitly')
                activity = match[1]
            if activity not in {f'A{i:02}' for i in range(1, 13)}:
                raise ValueError(f'Unexpected Mendeley activity: {activity}')
            ts_parts.append(d.timestamp_low.to_numpy(float))
            cols = [f'csi_1_{rx}_{c}' for rx in range(1, 4) for c in range(1, 31)]
            amp_parts.append(np.array([[abs(complex(str(s).replace('+-', '-').replace('i', 'j'))) for s in row] for row in d[cols].itertuples(index=False, name=None)], dtype=np.float32))
            label_parts.append(np.full(len(d), activity in {'A02', 'A05'}, np.int8))
        ts, amp, raw_labels = np.concatenate(ts_parts), np.concatenate(amp_parts), np.concatenate(label_parts)
        if len(ts) < 2 or not np.isfinite(ts).all():
            raise ValueError('Mendeley timestamps must be finite with at least two rows')
        delta = np.diff(ts)
        bad = delta <= 0
        repaired = np.r_[0., np.cumsum(np.where(bad, 1e6/fs, delta))]
        n = int(record.get('resampled_rows', int(np.floor(repaired[-1]*fs/1e6))+1))
        if n < 2:
            raise ValueError('resampled_rows must be at least 2')
        axis = repaired/repaired[-1]
        grid = np.linspace(0, 1, n)
        amplitude = np.stack([np.interp(grid, axis, x) for x in amp.T], axis=1).astype(np.float32)
        labels = raw_labels[nearest(axis, grid)]
        audit.update(raw_rows=len(ts), repaired_gaps=int(bad.sum()), grid_length_source='explicit' if 'resampled_rows' in record else 'timestamp_duration')
    else:
        raise ValueError(f'Unsupported raw format: {fmt}')
    if not np.isfinite(fs) or fs <= 0 or amplitude.ndim != 2 or amplitude.shape[1] not in (30, 90) or not np.isfinite(amplitude).all() or len(amplitude) < 2:
        raise ValueError('Expected finite regular amplitude with shape time × 30 or time × 90 and positive fs')
    if labels is not None and labels.shape != (len(amplitude),):
        raise ValueError('One label is required per resampled timestamp')
    audit.update(resampled_rows=len(amplitude), channels=amplitude.shape[1], fs=fs)
    return amplitude, labels, fs, audit


def events(labels):
    mask = np.asarray(labels) == 1
    edges = np.diff(np.r_[False, mask, False].astype(int))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def window_labels(starts, n, labels, fs, overlap=.5, guard=.5):
    if not 0 <= overlap < 1 or guard < 0:
        raise ValueError('overlap must be in [0,1), guard must be nonnegative')
    runs = events(labels)
    keep, y = [], []
    g = round(guard*fs)
    for i, start in enumerate(starts):
        end = start+n
        positive = any(start <= (a+b-1)/2 < end and max(0, min(end, b)-max(start, a))/min(n, b-a) > overlap for a, b in runs)
        guarded = any(min(end, b+g) > max(start, a-g) for a, b in runs)
        if positive or not guarded:
            keep.append(i)
            y.append(int(positive))
    return np.asarray(keep, int), np.asarray(y, np.int8)


def validate_records(records, base):
    ids, groups, paths = set(), {}, {}
    for r in records:
        rid = r['id']
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', rid) or rid in ids or rid in {'.', '..'}:
            raise ValueError(f'Invalid or duplicate recording id: {rid}')
        ids.add(rid)
        role, split = r['role'], r['split']
        allowed = {'source': {'train', 'val'}, 'target': {'train', 'val', 'test'}, 'external': {'external'}}
        if role not in allowed or split not in allowed[role]:
            raise ValueError(f'Invalid role/split: {role}/{split}')
        group = r.get('group', rid)
        if group in groups and groups[group] != (role, split):
            raise ValueError(f'Group {group} crosses training/evaluation boundaries')
        groups[group] = (role, split)
        raw_paths = [r['path']] if r['format'] != 'mendeley' else [s['path'] for s in r['segments']]
        for value in raw_paths:
            path = resolve(base, value)
            if path in paths:
                raise ValueError(f'Raw file is repeated in records: {path.name}')
            if not path.is_file():
                raise FileNotFoundError(path)
            paths[path] = rid
    if not records:
        raise ValueError('No recordings provided')
    return paths
