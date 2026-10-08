#!/usr/bin/env python3
"""Self-check for bin/cmon. Run: python3 test/test_agent_monitor.py

Plain asserts, no framework. Each test_* function gets a fresh temp state dir and a
stub cmux that records its argv to <dir>/calls and answers the two list commands.
"""
import importlib.machinery, importlib.util, json, os, subprocess, tempfile, time

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "cmon")
_loader = importlib.machinery.SourceFileLoader("m", SCRIPT)
m = importlib.util.module_from_spec(importlib.util.spec_from_loader("m", _loader)); _loader.exec_module(m)

GROUPS = '{"groups":[{"name":"G","anchor_workspace_id":"A","member_workspace_ids":["A","W"]}]}'
NAMES = '{"workspaces":[{"id":"A","title":"Anchor"},{"id":"W","title":"作業"},{"id":"Q","title":"Quiet"}]}'


def stub(d, sleep=0, fail=False):
    """Write a fake cmux into d; returns env for subprocesses."""
    body = "#!/bin/sh\necho \"$@\" >> \"%s/calls\"\n" % d
    if sleep:
        body += "sleep %s\n" % sleep
    if fail:
        body += "exit 1\n"
    body += "[ \"$1\" = rpc ] && echo '%s'\n[ \"$2\" = workspace ] && echo '%s'\nexit 0\n" % (GROUPS, NAMES)
    p = os.path.join(d, "cmux"); open(p, "w").write(body); os.chmod(p, 0o755)
    return dict(os.environ, CMON_DIR=d, CMUX_BUNDLED_CLI_PATH=p)


def fire(env, payload, ws="W", wait=True):
    env = dict(env); env.pop("CMUX_WORKSPACE_ID", None)  # the test itself may run inside cmux
    if ws:
        env["CMUX_WORKSPACE_ID"] = ws
    pr = subprocess.Popen([SCRIPT, "hook", "claude"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, env=env)
    pr.stdin.write(json.dumps(payload)); pr.stdin.close()
    if not wait:
        return pr
    out = pr.stdout.read(); pr.wait()
    assert pr.returncode == 0 and out.strip() == "{}", (pr.returncode, out, pr.stderr.read())
    return pr


def calls(d):
    try:
        return open(os.path.join(d, "calls")).read()
    except OSError:
        return ""


def state(d):
    return json.load(open(os.path.join(d, "state.json")))


_n = [0]
def ev(session, event, ws="W", **kw):
    _n[0] += 1
    return {"session": session, "event": event, "ws": ws, "seq": kw.pop("seq", _n[0]), **kw}


# ------------------------------------------------------------------ reducer

def test_reducer_transitions():
    S = {}
    m.reduce(S, ev("a", "SessionStart"));            assert m.ws_state(S, {"W"}) == "idle"
    m.reduce(S, ev("a", "UserPromptSubmit"));        assert m.ws_state(S, {"W"}) == "running"
    m.reduce(S, ev("a", "PreToolUse", tool="AskUserQuestion")); assert m.ws_state(S, {"W"}) == "needs_input"
    m.reduce(S, ev("a", "PostToolUse", tool="AskUserQuestion")); assert m.ws_state(S, {"W"}) == "running"
    m.reduce(S, ev("a", "PreToolUse", tool="Bash"))
    m.reduce(S, ev("a", "PermissionRequest", tool="Bash")); assert m.ws_state(S, {"W"}) == "needs_input"
    m.reduce(S, ev("a", "PostToolUse", tool="Bash"));  assert m.ws_state(S, {"W"}) == "running"
    m.reduce(S, ev("a", "Stop", bg=0));              assert m.ws_state(S, {"W"}) == "idle"
    m.reduce(S, ev("a", "SessionEnd"));              assert "a" not in S and m.ws_state(S, {"W"}) == "none"


def test_reducer_order_guard():
    S = {}
    m.reduce(S, ev("a", "UserPromptSubmit"))
    late = ev("a", "PreToolUse", tool="Bash"); m.reduce(S, ev("a", "Stop", bg=0)); m.reduce(S, late)
    assert S["a"]["status"] == "idle", "stale PreToolUse applied after Stop"
    late_end = ev("a", "SessionEnd"); m.reduce(S, ev("a", "UserPromptSubmit")); m.reduce(S, late_end)
    assert "a" in S, "stale SessionEnd removed a live session"


def test_reducer_waiting_vs_teammates():
    S = {}
    m.reduce(S, ev("a", "UserPromptSubmit")); m.reduce(S, ev("a", "Stop", bg=1)); assert S["a"]["status"] == "waiting"
    assert m.ws_state(S, {"W"}) == "idle", "a lone waiter is idle"
    m.reduce(S, ev("t", "UserPromptSubmit")); assert m.ws_state(S, {"W"}) == "running"
    m.reduce(S, ev("t", "Stop", bg=0));       assert m.ws_state(S, {"W"}) == "idle"
    m.reduce(S, ev("t", "PreToolUse", tool="AskUserQuestion")); assert m.ws_state(S, {"W"}) == "needs_input"


def test_reducer_scoping_and_liveness():
    S = {}
    m.reduce(S, ev("a", "Stop", bg=0)); m.reduce(S, ev("c", "UserPromptSubmit", ws="X"))
    assert m.ws_state(S, {"W"}) == "idle" and m.ws_state(S, {"W", "X"}) == "running" and m.ws_state(S, {"Z"}) == "none"
    S["c"]["pid"] = 2**22 - 1; S["a"]["pid"] = os.getpid(); m.drop_dead(S); assert "c" not in S and "a" in S
    assert m.pad("作業", 6) == "作業  " and m.pad("ab", 4) == "ab  "


# ------------------------------------------------------------------ hook path

def test_hook_transitions_and_pruning():
    d = tempfile.mkdtemp(); env = stub(d)
    tp = os.path.join(d, "t.jsonl"); open(tp, "w").write('{"type":"x"}\n{"agentName":"xbbr-T01","type":"user"}\n')
    fire(env, {"session_id": "h1", "hook_event_name": "UserPromptSubmit", "transcript_path": tp})
    fire(env, {"session_id": "h1", "hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion"})
    fire(env, {"session_id": "h1", "hook_event_name": "Stop", "background_tasks": [{"type": "teammate"}], "session_crons": []})
    fire(env, {"session_id": "zz", "hook_event_name": "UserPromptSubmit"}, ws="Z")  # unknown workspace: pruned
    c = calls(d)
    assert "workspace status set working --workspace W" in c
    assert "notify --title Input needed" in c and "--workspace A" in c
    assert "notify --title All agents finished" in c, "teammate-only background work must count as idle"
    assert "workspace status set done --workspace W" in c
    assert c.count("rpc workspace.group.list") == 1, "cmux lists cached across hooks"
    st = state(d)
    assert st["sessions"]["h1"]["name"] == "xbbr-T01", "teammate name read from transcript"
    assert "zz" not in st["sessions"] and "Z" not in st["lanes"], "state pruned to existing workspaces"
    assert set(st["lanes"]) <= {"A", "W", "Q"} and set(st["last"]) == {"A", "Q"}
    assert sorted(os.listdir(d)) == ["calls", "cmux", "lock", "state.json", "t.jsonl"], "no audit log, no leftovers"


def test_hook_outside_cmux_and_cmux_failure():
    d = tempfile.mkdtemp(); env = stub(d)
    fire(env, {"session_id": "h1", "hook_event_name": "UserPromptSubmit"}, ws=None)
    assert not os.path.exists(os.path.join(d, "state.json")), "no workspace id: nothing written"
    d = tempfile.mkdtemp(); env = stub(d, fail=True)
    fire(env, {"session_id": "h1", "hook_event_name": "UserPromptSubmit"})
    assert state(d)["sessions"]["h1"]["status"] == "running", "state kept when cmux fails"


def test_hook_parallel_no_lost_updates():
    d = tempfile.mkdtemp(); env = stub(d)
    procs = [fire(env, {"session_id": "s%02d" % i, "hook_event_name": "UserPromptSubmit"}, wait=False) for i in range(40)]
    for p in procs:
        p.stdout.read(); p.wait(); assert p.returncode == 0
    assert len(state(d)["sessions"]) == 40, "parallel hooks lost sessions"


def test_hook_cmux_runs_outside_lock():
    d = tempfile.mkdtemp(); env = stub(d, sleep=1)
    p1 = fire(env, {"session_id": "h1", "hook_event_name": "UserPromptSubmit"}, wait=False)
    time.sleep(0.3)
    t0 = time.time(); fire(env, {"session_id": "h2", "hook_event_name": "UserPromptSubmit"}); dt = time.time() - t0
    p1.stdout.read(); p1.wait()
    # h2 must not wait for h1's slow cmux calls (lock released before side effects); it only pays its own
    assert dt < 2.5, "second hook waited %.1fs behind the first hook's cmux calls" % dt


# ------------------------------------------------------------------ status / watch

def test_status_tree():
    d = tempfile.mkdtemp(); env = stub(d)
    tp = os.path.join(d, "t.jsonl"); open(tp, "w").write('{"agentName":"xbbr-T01"}\n')
    fire(env, {"session_id": "lead", "hook_event_name": "Stop", "background_tasks": [{"type": "teammate"}]}, ws="A")
    fire(env, {"session_id": "mate", "hook_event_name": "UserPromptSubmit", "transcript_path": tp})
    out = subprocess.run([SCRIPT, "status"], capture_output=True, text=True, env=env).stdout.splitlines()
    assert out[0] == "G  [running]", "anchor's own session counts in the aggregate"
    assert not any("Anchor" in l for l in out), "group anchor is the group line, not a branch"
    assert out[1].startswith("  └─ 作業") and "running" in out[1] and "working" in out[1]
    assert out[2].strip() == "└─ xbbr-T01  claude  running", out[2]
    assert "(ungrouped)  [none]" in out and any(l.startswith("  └─ Quiet") for l in out)


def test_watch_redraws_once_per_hook():
    d = tempfile.mkdtemp(); env = stub(d)
    log = open(os.path.join(d, "out.txt"), "w")
    w = subprocess.Popen([SCRIPT, "watch"], stdout=log, stderr=subprocess.STDOUT, env=env)
    time.sleep(0.8)
    fire(env, {"session_id": "h1", "hook_event_name": "UserPromptSubmit"}); time.sleep(0.6)
    fire(env, {"session_id": "h1", "hook_event_name": "Stop"}); time.sleep(0.6)
    w.terminate(); w.wait(); log.close()
    out = open(os.path.join(d, "out.txt")).read()
    assert out.count("watching") == 3, "expected initial draw + one redraw per hook, got %d" % out.count("watching")
    assert "h1  claude  idle" in out


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
