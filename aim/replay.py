#!/usr/bin/env python3
"""Replay recorded observations, without opening any device or sending movement."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path

from feedback import decide, output_check
from policy import Controller, Settings


def replay(path, overrides=None):
    controller = None
    original = None
    pending = None
    counts = Counter()
    mismatches = []
    active, epoch = False, 0
    overridden = bool(overrides)
    metadata_seen = False
    last_kind = None
    with Path(path).open() as recording:
        for line_number, line in enumerate(recording, 1):
            event = json.loads(line)
            kind = event['type']
            last_kind = kind
            if kind == 'metadata':
                if metadata_seen or event.get('recording_version') != 1:
                    raise ValueError('Unsupported or repeated recording metadata')
                metadata_seen = True
                settings = dict(event['settings']); settings.update(overrides or {})
                if not all(type(v) in (int, float) and math.isfinite(v) for v in settings.values()):
                    raise ValueError('Settings must be finite')
                controller = Controller(Settings(**settings))
                original = Controller(Settings(**event['settings']))
                continue
            if controller is None:
                raise ValueError('Recording must begin with metadata')
            if kind == 'gate':
                active, epoch = event['active'], event['epoch']
                controller.reset(); original.reset()
            elif kind == 'observation':
                if pending is not None and pending['decision']['reason'] == 'move':
                    counts['no_recorded_output_boundary'] += 1
                frame = event['frame']
                actual = decide(controller, frame, event['at_s'], active, epoch)
                expected = decide(original, frame, event['at_s'], active, epoch)
                if expected != event['decision']:
                    mismatches.append(dict(line=line_number, stage='decision', expected=event['decision'], actual=expected))
                if active != event['active'] or epoch != event['epoch']:
                    mismatches.append(dict(line=line_number, stage='activation'))
                counts['observations'] += 1
                counts['decision_changed'] += actual != event['decision']
                counts['reason_' + actual['reason']] += 1
                pending = dict(frame=frame, decision=actual, expected=expected)
            elif kind == 'output':
                if pending is None or pending['frame']['index'] != event['frame_index']:
                    raise ValueError('Output has no matching observation')
                frame = pending['frame']
                expected = output_check(original, frame, event['checked_at_s'], active, epoch)
                check_matches = all(expected[k] == event[k] for k in expected)
                if not check_matches:
                    mismatches.append(dict(line=line_number, stage='output_check'))
                result = event['result']
                recorded_status = result['output_status']
                successful = recorded_status in ('sent', 'simulated')
                valid = check_matches and event['active'] == active and event['epoch'] == epoch
                valid = valid and pending['expected']['reason'] == 'move'
                if successful:
                    valid = valid and expected['reason'] == 'ready_to_send' and result['reason'] == 'move'
                    valid = valid and all(result[k] == pending['expected'][k] for k in ('dx', 'dy'))
                    valid = valid and result.get('send_start_s') == event['checked_at_s']
                elif recorded_status == 'dropped':
                    valid = valid and expected['reason'] != 'ready_to_send' and result['reason'] == expected['reason']
                    valid = valid and result['dx'] == 0 and result['dy'] == 0
                elif recorded_status == 'failed':
                    valid = valid and expected['reason'] == 'ready_to_send' and result['reason'] == 'output_failed'
                else:
                    valid = False
                if successful or recorded_status == 'failed':
                    completed = result.get('command_complete_s')
                    valid = valid and type(completed) in (int, float) and math.isfinite(completed) and completed >= event['checked_at_s']
                if not valid:
                    mismatches.append(dict(line=line_number, stage='output_result'))
                if valid and successful:
                    original.commit_move(result['send_start_s'])
                if valid and pending['decision']['reason'] == 'move':
                    check = output_check(controller, frame, event['checked_at_s'], active, epoch)
                    if check['reason'] == 'ready_to_send' and recorded_status != 'failed':
                        controller.commit_move(event['checked_at_s'])
                        counts['hypothetical_sends' if overridden else 'verified_sends'] += 1
                pending = None
            elif kind == 'fault':
                counts['faults'] += 1
            elif kind != 'stop':
                raise ValueError(f'Unsupported replay event {kind}')
    if not metadata_seen:
        raise ValueError('Empty recording')
    if last_kind not in ('stop', 'fault'):
        mismatches.append(dict(stage='incomplete_recording'))
    if pending is not None and pending['decision']['reason'] == 'move':
        counts['no_recorded_output_boundary'] += 1
    return dict(counts=counts, mismatches=mismatches, overrides=overrides or {},
                interpretation='Fixed observations and recorded output opportunities; not a new closed-loop trajectory')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recording', type=Path)
    parser.add_argument('--gain-x', type=float)
    parser.add_argument('--gain-y', type=float)
    parser.add_argument('--deadzone', type=float)
    parser.add_argument('--confirm-frames', type=int)
    args = parser.parse_args()
    overrides = {key: getattr(args, key) for key in ('gain_x', 'gain_y', 'deadzone', 'confirm_frames') if getattr(args, key) is not None}
    if any(not math.isfinite(v) for v in overrides.values()) or overrides.get('deadzone', 0) < 0 or overrides.get('confirm_frames', 1) < 1:
        parser.error('Overrides must be finite; deadzone >= 0; confirm-frames >= 1')
    result = replay(args.recording, overrides)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(bool(result['mismatches']))


if __name__ == '__main__':
    raise SystemExit(main())
