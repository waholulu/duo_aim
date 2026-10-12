"""Hardware-independent decision and final output boundary, also used by replay."""
import math


def frame_age_ms(frame, now):
    values = [now, frame['pts_s'], frame['host_start_s'], frame['callback_to_coords_ms']]
    if not all(type(v) in (int, float) and math.isfinite(v) for v in values):
        raise ValueError('Non-finite frame clock')
    age = (now - frame['pts_s']) * 1000
    queue_age = (now - frame['host_start_s']) * 1000 - frame['callback_to_coords_ms']
    if age < -5 or queue_age < -5:
        raise ValueError('Host clock mismatch; refusing mouse output')
    return max(0, age)


def decide(controller, frame, now, active, epoch):
    if frame['gate_epoch'] != epoch:
        return dict(reason='old_epoch', dx=0, dy=0, head=None)
    if not active:
        return dict(reason='inactive', dx=0, dy=0, head=None)
    return controller.step(frame, now, age_ms=frame_age_ms(frame, now))


def output_check(controller, frame, now, active, epoch):
    age = frame_age_ms(frame, now)
    if not active:
        reason = 'released_before_move'
    elif frame['gate_epoch'] != epoch:
        reason = 'old_epoch'
    elif age > controller.cfg.max_age_ms:
        reason = 'stale_before_move'
    elif now - controller.last_move < controller.cfg.interval_ms / 1000:
        reason = 'rate_limited'
    else:
        reason = 'ready_to_send'
    return dict(reason=reason, send_age_ms=age)


def dispatch(controller, frame, decision, held, transition, state, output=None,
             clock=None, observe=None):
    """One serial owner; no I/O between final check and output invocation.

    state returns (active, epoch). observe receives a completed transaction,
    including failed output, for diagnostics and deterministic replay.
    """
    import time
    clock = clock or time.monotonic
    result = dict(decision, output_status='not_requested')
    if decision['reason'] != 'move':
        return result
    result['output_status'] = 'suggested'
    query_start = clock()
    pressed = held()
    query_end = clock()
    if not pressed:
        transition(False)
    active, epoch = state()
    send_start = clock()
    check = output_check(controller, frame, send_start, active, epoch)
    transaction = dict(check, query_start_s=query_start, query_end_s=query_end,
                       checked_at_s=send_start, active=active, epoch=epoch)
    result.update(check, query_ms=(query_end-query_start)*1000)
    try:
        if check['reason'] == 'ready_to_send':
            if output is not None:
                output.move(decision['dx'], decision['dy'])
            controller.commit_move(send_start)
            result.update(reason='move', output_status='sent' if output is not None else 'simulated',
                          send_start_s=send_start, command_complete_s=clock())
        else:
            result.update(output_status='dropped', dx=0, dy=0)
    except BaseException:
        result.update(output_status='failed', reason='output_failed', command_complete_s=clock())
        raise
    finally:
        transaction.update(result=result)
        if observe:
            observe(transaction)
    return result
