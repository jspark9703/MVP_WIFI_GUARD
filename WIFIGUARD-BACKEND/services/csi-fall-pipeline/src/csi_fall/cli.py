"""Command-line entry points; paths are relative to the JSON configuration."""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
import torch
from .io import load_record, native_window_starts, write_json
from .prepare import prepare
from .engine import train, normalize
from .features import extract, CHOICES
from .models import FallModel


def select_device(value):
    if value == 'auto':
        return 'cuda' if torch.cuda.is_available() else 'cpu'
    if value == 'cuda' and not torch.cuda.is_available():
        raise ValueError('CUDA requested but not available')
    return value


@torch.no_grad()
def predict(checkpoint, record_path, output, device='cpu', batch_size=16):
    if batch_size < 1:
        raise ValueError('batch_size must be positive')
    ck = torch.load(checkpoint, map_location=device, weights_only=False)
    cfg, norm = ck['config'], ck['normalization']
    model = FallModel(cfg['feature'], cfg['cwt'], cfg['backbone']).to(device).eval()
    model.load_state_dict(ck['model'])
    path = Path(record_path).resolve()
    record = json.loads(path.read_text())
    amp, _, fs, audit = load_record(record, path.parent)
    starts = native_window_starts(len(amp), fs, 3., cfg['stride_seconds'])
    if not len(starts):
        raise ValueError('Recording is shorter than 3 seconds')
    rows, embeddings = [], []
    for rx in range(amp.shape[1]//30):
        for begin in range(0, len(starts), batch_size):
            selected = starts[begin:begin+batch_size]
            windows = np.stack([amp[s:s+round(3*fs), rx*30:(rx+1)*30] for s in selected])
            f = extract(windows, fs, cfg['feature'], cfg['cwt'], device)
            x = torch.from_numpy(normalize(f['secondary'], norm['secondary'], cfg['feature'] == 'scalars')).to(device)
            c = torch.from_numpy(normalize(f['cwt'], norm['cwt'])).to(device) if cfg['cwt'] else None
            logits, z, _ = model(x, c)
            p = logits.softmax(-1)[:, 1].cpu().numpy()
            embeddings.append(z.cpu().numpy())
            for start, probability in zip(selected, p):
                rows.append(dict(recording_id=record.get('id', path.stem), rx=rx+1, window_start=int(start), start_seconds=start/fs, end_seconds=start/fs+3, probability_fall=float(probability), predicted_fall=int(probability >= ck.get('threshold', .5))))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(output, index=False)
    np.savez(output.with_suffix('.embeddings.npz'), embedding=np.concatenate(embeddings))
    write_json(output.with_suffix('.audit.json'), dict(preprocessing=audit, feature=cfg['feature'], cwt=cfg['cwt'], windows=len(rows), threshold=ck.get('threshold', .5), label_used=False))
    print(f'Predictions saved: {output}', flush=True)
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description='Raw CSI → ACF/CWT → training → raw prediction')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'run'):
        p = sub.add_parser(name)
        p.add_argument('config', type=Path)
        p.add_argument('--device', choices=['auto', 'cpu', 'cuda'], default='auto')
        p.add_argument('--feature-batch', type=int, default=16)
    p = sub.add_parser('train')
    p.add_argument('output', type=Path)
    p.add_argument('--initialize', type=Path)
    p.add_argument('--device', choices=['auto', 'cpu', 'cuda'], default='auto')
    p = sub.add_parser('predict')
    p.add_argument('checkpoint', type=Path)
    p.add_argument('--record', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--device', choices=['auto', 'cpu', 'cuda'], default='auto')
    p.add_argument('--batch-size', type=int, default=16)
    p = sub.add_parser('demo')
    p.add_argument('directory', type=Path)
    p.add_argument('--feature', choices=CHOICES, default='channel_mean')
    p.add_argument('--cwt', action='store_true')
    p = sub.add_parser('index')
    p.add_argument('--local-root', type=Path)
    p.add_argument('--mendeley-root', type=Path)
    p.add_argument('--external-root', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--feature', choices=CHOICES, default='channel_mean')
    p.add_argument('--cwt', action='store_true')
    p.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    torch.set_num_threads(4)
    if args.command == 'demo':
        from .demo import generate
        generate(args.directory, args.feature, args.cwt)
    elif args.command == 'index':
        from .index import build
        build(args)
    elif args.command in ('prepare', 'run'):
        device = select_device(args.device)
        output = prepare(args.config, device, args.feature_batch)
        if args.command == 'run':
            train(output, device)
    elif args.command == 'train':
        train(args.output, select_device(args.device), args.initialize)
    else:
        predict(args.checkpoint, args.record, args.output, select_device(args.device), args.batch_size)


if __name__ == '__main__':
    main()
