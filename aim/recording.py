"""Small JSONL records shared by runtime, replay and summaries."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from runtime import PROTOCOL_VERSION


def model_fingerprint(path):
    path = Path(path)
    digest = hashlib.sha256()
    files = sorted(p for p in path.rglob('*') if p.is_file()) if path.is_dir() else [path]
    if not files:
        raise ValueError('Model directory is empty')
    for item in files:
        name = str(item.relative_to(path)) if path.is_dir() else item.name
        digest.update(name.encode() + b'\0')
        with item.open('rb') as source:
            for block in iter(lambda: source.read(1024*1024), b''):
                digest.update(block)
        digest.update(b'\0')
    return digest.hexdigest()


def metadata(args, cfg, model):
    return dict(type='metadata', recording_version=1, protocol_version=PROTOCOL_VERSION,
                model_path=str(model), model_sha256=model_fingerprint(model), settings=asdict(cfg),
                crop=args.crop, backend=args.backend, activation=args.activation, dry_run=not args.live,
                clock='seconds since run start, host monotonic', warmup=30)


def relative_frame(frame, origin):
    result = dict(frame)
    for key in ('pts_s', 'host_start_s'):
        result[key] -= origin
    return result


def write_jsonl(file, row):
    file.write(json.dumps(row, allow_nan=False, separators=(',', ':')) + '\n')
