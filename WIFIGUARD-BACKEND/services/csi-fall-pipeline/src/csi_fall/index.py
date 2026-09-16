"""Create a fresh deterministic recording split without prior experiment manifests."""
import re
from collections import defaultdict
import numpy as np
from .io import write_json


def assign(records, role, rng):
    if role == 'external':
        for record in records:
            record.update(role=role, split='external')
        return records
    needed = 2 if role == 'source' else 3
    if len(records) < needed:
        raise ValueError(f'{role} needs at least {needed} independent recordings/tasks')
    order = rng.permutation(len(records))
    nval = max(1, round(.2*len(records)))
    ntest = max(1, round(.2*len(records))) if role == 'target' else 0
    for rank, idx in enumerate(order):
        split = 'val' if rank < nval else 'test' if rank < nval+ntest else 'train'
        records[idx].update(role=role, split=split)
    return records


def build(args):
    rng = np.random.default_rng(args.seed)
    records = []
    if args.mendeley_root:
        groups = defaultdict(list)
        pattern = re.compile(r'^(E\d+)_(S\d+)_(C\d+)_(A\d+)_(T\d+)\.csv$')
        for path in sorted(args.mendeley_root.resolve().rglob('*.csv')):
            match = pattern.fullmatch(path.name)
            if not match:
                raise ValueError(f'Unrecognized Mendeley filename: {path.name}')
            e, s, c, a, t = match.groups()
            groups['_'.join([e, s, c, t])].append(dict(path=str(path), activity=a))
        source = [dict(id='M_'+key, format='mendeley', fs=320., segments=sorted(values, key=lambda x: int(x['activity'][1:]))) for key, values in sorted(groups.items())]
        records += assign(source, 'source', rng)
    for root, role in [(args.local_root, 'target'), (args.external_root, 'external')]:
        if root:
            local = [dict(id=role+'_'+p.stem, format='local', path=str(p), fs=500/3) for p in sorted(root.resolve().rglob('*.csv'))]
            records += assign(local, role, rng)
    if not records:
        raise ValueError('Specify a raw dataset root')
    if args.output.exists():
        raise FileExistsError(args.output)
    write_json(args.output, dict(output='outputs/experiment', feature=args.feature, cwt=args.cwt, backbone='compact', records=records))
    print(f'Configuration with {len(records)} records: {args.output}. Review roles, splits, segment order, and sampling rates before running.')
