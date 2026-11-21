"""Pause, resume and step-through: the runner holds before its next step, lets exactly one through, or carries on."""
import threading
import time
from pathlib import Path

from aiauto.models import RunConfig
from aiauto.nlp.parser import parse_text
from aiauto.runner import Runner

URL = (Path(__file__).resolve().parent.parent / "examples" / "extension" / "order.html").as_uri()
TEXT = f'Open {URL}\nType "Asha" into Name\nSelect "2" from Quantity\nClick Place order\nVerify page contains "Thanks, Asha"'


def start(tmp_path, paused):
    events = []
    test, _ = parse_text(TEXT, {}, use_llm=False)
    r = Runner(test, RunConfig(record_video=False, use_llm=False, timeout_ms=5000, start_paused=paused), tmp_path, on_event=events.append)
    t = threading.Thread(target=r.run, daemon=True)
    t.start()
    return r, t, events


def wait_for(events, pred, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if any(pred(e) for e in list(events)):
            return
        time.sleep(0.1)
    raise AssertionError("timed out: " + str([e["type"] for e in events][-8:]))


def finished(events):
    return [e["index"] for e in events if e["type"] == "step_finished"]


def test_stepping_lets_exactly_one_step_through_at_a_time(tmp_path):
    r, t, ev = start(tmp_path, paused=True)
    wait_for(ev, lambda e: e["type"] == "paused" and e["index"] == 0)
    assert finished(ev) == []                                         # nothing ran: it is held before the first step
    r.step()
    wait_for(ev, lambda e: e["type"] == "paused" and e["index"] == 1)
    assert finished(ev) == [0]                                        # one step went through, then it held again
    r.step()
    wait_for(ev, lambda e: e["type"] == "paused" and e["index"] == 2)
    assert finished(ev) == [0, 1]
    r.resume()                                                        # resume: the rest runs without holding
    t.join(90)
    assert finished(ev) == [0, 1, 2, 3, 4]
    assert [e["status"] for e in ev if e["type"] == "run_finished"] == ["passed"]


def test_pause_holds_a_running_test_before_its_next_step_and_stop_still_works_while_held(tmp_path):
    r, t, ev = start(tmp_path, paused=False)
    r.pause()
    wait_for(ev, lambda e: e["type"] == "paused")
    n = len(finished(ev))
    time.sleep(1.0)
    assert len(finished(ev)) == n                                     # held: no further step runs
    r.cancel.set()                                                    # Stop works while it is paused
    t.join(60)
    assert [e["status"] for e in ev if e["type"] == "run_finished"] == ["cancelled"]
