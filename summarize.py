"""Summarize only completed capture and inference benchmarks."""
import argparse
import json
from pathlib import Path
import numpy as np

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('files', nargs='*', type=Path, help='Result JSON files; defaults to results/*.json')
args = ap.parse_args()
files = args.files or sorted(Path('results').glob('*.json'))
summary = {}
timings = ['preprocess_ms', 'inference_ms', 'postprocess_ms', 'callback_to_coords_ms', 'pts_to_callback_ms', 'pts_to_coords_ms']
for path in files:
    if path.name == 'summary.json' or path.name.startswith('INVALID-'):
        continue
    try:
        data = json.loads(path.read_text())
        rows = data['rows']
        requested = data['requested_samples']
        if data['status'] != 'completed' or not rows or len(rows) < requested:
            print(f'SKIP {path}: incomplete benchmark')
            continue
        valid = [row for row in rows if row['head_valid']]
        result = {'samples': len(rows), 'valid_heads': len(valid),
                  'model_size': data['model_size'], 'crop': data['crop'],
                  'compute_units': data['compute_units'], 'dropped_callbacks': data['dropped_callbacks']}
        for key in timings:
            result[key] = dict(zip(['min', 'median', 'p95', 'max'],
                                   map(float, np.percentile([row[key] for row in rows], [0, 50, 95, 100]))))
        if valid:
            result['fastest_valid_head'] = min(valid, key=lambda row: row['callback_to_coords_ms'])
        elapsed = rows[-1]['host_start_s'] - rows[0]['host_start_s']
        result['processed_fps'] = (len(rows)-1)/elapsed if len(rows)>1 and elapsed>0 else None
        pts = np.array([row['pts_s'] for row in rows])
        result['pts_interval_median_ms'] = float(np.median(np.diff(pts))*1000) if len(rows)>1 else None
        summary[path.stem] = result
    except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
        print(f'SKIP {path}: invalid result ({error})')

Path('results').mkdir(exist_ok=True)
Path('results/summary.json').write_text(json.dumps(summary, indent=2))
for name, result in summary.items():
    fps = round(result['processed_fps'], 2) if result['processed_fps'] is not None else 'n/a'
    print(name, 'n=', result['samples'], 'heads=', result['valid_heads'],
          'fps=', fps, 'processing=', result['callback_to_coords_ms'],
          'pts_to_coords=', result['pts_to_coords_ms'])
