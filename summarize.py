#!/usr/bin/env python3
"""Summarize explicit benchmark JSON or runtime JSONL paths; no device access."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path


def stats(values):
    values = sorted(v for v in values if type(v) in (int, float) and math.isfinite(v))
    if not values:
        return None
    def percentile(p):
        index = (len(values)-1)*p
        lo = int(index); hi = min(lo+1, len(values)-1)
        return values[lo] + (values[hi]-values[lo])*(index-lo)
    return dict(samples=len(values), min=values[0], median=percentile(.5), p95=percentile(.95), max=values[-1])


def summarize(path):
    path = Path(path)
    if path.name.startswith('INVALID-'):
        raise ValueError('Refusing invalid prototype benchmark')
    if path.name.endswith('.replay.jsonl'):
        raise ValueError('Use the runtime log, not the replay recording')
    if path.suffix == '.jsonl':
        events = [json.loads(line) for line in path.read_text().splitlines()]
        rows = [e for e in events if e.get('type') == 'decision' or ('reason' in e and 'frame' in e)]
        counts = Counter(e.get('reason') for e in rows)
        sent = [e for e in rows if e.get('output_status') in ('sent', 'simulated')]
        result = dict(samples=len(rows), faults=sum(e.get('type') == 'fault' for e in events),
                      stale_drops=counts['stale']+counts['stale_before_move'],
                      released_drops=counts['released_before_move'], old_epoch_drops=counts['old_epoch'],
                      output_statuses=dict(Counter(e.get('output_status', 'legacy_unknown') for e in rows)))
        fields = ['preprocess_ms', 'inference_ms', 'postprocess_ms', 'processing_ms',
                  'pts_to_coords_ms', 'pts_to_controller_ms', 'query_ms']
        for field in fields:
            result[field] = stats([e.get(field) for e in rows])
        result['send_age_ms'] = stats([e.get('send_age_ms') for e in sent])
        result['send_interval_ms'] = stats([(b['send_start_s']-a['send_start_s'])*1000 for a, b in zip(sent, sent[1:])])
        result['command_ms'] = stats([(e['command_complete_s']-e['send_start_s'])*1000 for e in sent])
        return result
    data = json.loads(path.read_text())
    rows = data['rows']
    valid = [r for r in rows if r['head_valid']]
    result = {k: data.get(k) for k in ('model_size', 'crop', 'compute_units', 'dropped_callbacks', 'status', 'failed')}
    result.update(samples=len(rows), valid_heads=len(valid))
    for key in ['preprocess_ms', 'inference_ms', 'postprocess_ms', 'callback_to_coords_ms', 'pts_to_callback_ms', 'pts_to_coords_ms']:
        result[key] = stats([r[key] for r in rows])
    if valid:
        result['fastest_valid_head'] = min(valid, key=lambda r: r['callback_to_coords_ms'])
    elapsed = rows[-1]['host_start_s']-rows[0]['host_start_s'] if len(rows) > 1 else 0
    result['processed_fps'] = (len(rows)-1)/elapsed if elapsed > 0 else None
    result['pts_interval_ms'] = stats([(b['pts_s']-a['pts_s'])*1000 for a, b in zip(rows, rows[1:])])
    result['pts_interval_median_ms'] = result['pts_interval_ms']['median'] if result['pts_interval_ms'] else None
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', type=Path, nargs='+')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = {str(path): summarize(path) for path in args.inputs}
    text = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False)
    if args.output:
        args.output.write_text(text+'\n')
    print(text)


if __name__ == '__main__':
    main()
