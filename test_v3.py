"""
Plain-assert tests for the v3 generator, the trace tool and the NTUA adapter.

Usage:
  python test_v3.py

Covers: v2 output unchanged, reference regression, element counts with
composite projects, config validation, NTUA example verdicts, trace round
trips (both naming conventions), the simulator against an independent
closed-form check of the scheduling rules on random schedules, Python 3.8
syntax and smart quotes.
"""

import ast
import copy
import io
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ntua_adapter                                          # noqa: E402
import tacpn_trace as tt                                     # noqa: E402
from tacpn_generator_aegean import TACPNGenerator, ConfigError  # noqa: E402

NS = "{http://www.informatik.hu-berlin.de/top/pnml/ptNetb}"
EXAMPLE = os.path.join(HERE, "examples", "ntua_test")


def counts(xml):
    net = next(ET.fromstring(xml).iter(NS + "net"))
    return tuple(len(list(net.iter(NS + x))) for x in ("place", "transition", "arc"))


def ntua_example(schedule=None):
    with open(os.path.join(EXAMPLE, "test.json"), encoding="utf-8") as f:
        data = json.load(f)
    if schedule is None:
        schedule = ntua_adapter.load_schedule(
            os.path.join(EXAMPLE, "test_flp_schedule.json"))
    return ntua_adapter.build_config(data, schedule)[0]


def verdict(cfg):
    sim, v = tt.run_schedule(tt.Net(TACPNGenerator(cfg)))
    return sim, v


# ─── v2 compatibility ────────────────────────────────────────

def test_v2_output_unchanged():
    with open(os.path.join(HERE, "example_config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    with open(os.path.join(HERE, "TACPN_5flights_4tasks.tapn"), encoding="utf-8") as f:
        v2_output = f.read()
    assert TACPNGenerator(cfg).generate() == v2_output
    assert counts(v2_output) == (21, 30, 82)


def test_reference_regression():
    r = subprocess.run([sys.executable, "-B", "check_against_reference.py",
                        "TACPN_3flights_3tasks.tapn"], cwd=HERE,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True)
    assert r.returncode == 0, r.stdout


# ─── Element counts ──────────────────────────────────────────

def random_project_config(rng, intervals=True, single=False):
    n, K = rng.randint(1, 4), rng.randint(1, 5)
    ac = [f"A{i}" for i in range(1, n + 1)]
    life = rng.randint(20, 60)
    tasks = []
    for k in range(K):
        uniform = rng.random() < 0.3
        r0 = rng.randint(0, life + 10)
        t = {"id": f"t{k+1}", "guard": [0, life],
             "timer_invariants": {a: (r0 if uniform else rng.randint(0, life + 10))
                                  for a in ac}}
        if intervals:
            i0 = rng.randint(1, life + 10)
            t["interval_invariants"] = {a: (i0 if rng.random() < 0.3
                                            else rng.randint(1, life + 10)) for a in ac}
        tasks.append(t)
    projects = {}
    for a in ac:
        plist, start = [], 0
        for _ in range(rng.randint(0, 3)):
            d = rng.randint(0, 6)
            start = rng.randint(start, max(start, life - d))
            ks = rng.sample(range(1, K + 1), rng.randint(1, K))
            refs = [f"t{k}" if rng.random() < 0.5 else k for k in ks]
            plist.append({"tasks": refs, "duration": d, "start": start})
        projects[a] = plist
    if not any(projects.values()):
        projects[ac[0]] = [{"tasks": [1], "duration": 1, "start": 0}]
    cap = rng.randint(1, 3)
    return {"aircraft": ac, "flying_invariants": {a: life for a in ac},
            "entry_guard": [0, life], "bay_invariant": 0, "tasks": tasks,
            "crew_count": rng.choice([cap, rng.randint(1, cap)]), "hangar_count": cap,
            "lifespan": life, "single_task_blocks": single, "projects": projects}


def expected_counts(cfg):
    life, ac, K = cfg["lifespan"], cfg["aircraft"], len(cfg["tasks"])
    single = cfg["single_task_blocks"]
    ov = ovc = cyc = 0
    for t in cfg["tasks"]:
        inv = t["timer_invariants"]
        ov += ((1 if inv[ac[0]] < life else 0) if len(set(inv.values())) == 1
               else sum(inv[a] < life for a in ac))
        if "interval_invariants" in t:
            cyc += 1
            iv = t["interval_invariants"]
            ovc += ((1 if iv[ac[0]] < life else 0) if len(set(iv.values())) == 1
                    else sum(iv[a] < life for a in ac))
    occ = [len(set(_resolve(cfg, p["tasks"])))
           for a in ac for p in cfg["projects"].get(a, [])]
    places = 5 + K * (3 + single) + cyc + len(occ)
    trans = 2 + 2 * K * single + ov + ovc + 2 * len(occ)
    arcs = 6 + 9 * K * single + 2 * (ov + ovc) + sum(3 * s + 6 for s in occ)
    return places, trans, arcs


def _resolve(cfg, refs):
    ids = [t.get("id") for t in cfg["tasks"]]
    return [r - 1 if isinstance(r, int) else ids.index(r) for r in refs]


def test_element_counts_with_projects():
    rng = random.Random(1)
    for i in range(200):
        intervals = i % 2 == 0
        cfg = random_project_config(rng, intervals=intervals,
                                    single=(not intervals and rng.random() < 0.5))
        xml = TACPNGenerator(cfg).generate()
        assert counts(xml) == expected_counts(cfg), (i, counts(xml), expected_counts(cfg))
        # Arc endpoints exist, and every input arc of a project transition is timed
        net = next(ET.fromstring(xml).iter(NS + "net"))
        names = {e.get("id") for e in net if e.tag in (NS + "place", NS + "transition")}
        for arc in net.iter(NS + "arc"):
            assert arc.get("source") in names and arc.get("target") in names


# ─── Validation ──────────────────────────────────────────────

def test_validation_errors():
    good = ntua_example()
    TACPNGenerator(good)

    def bad(mutate, fragment):
        cfg = copy.deepcopy(good)
        mutate(cfg)
        try:
            TACPNGenerator(cfg)
        except ConfigError as e:
            assert fragment in str(e), (fragment, str(e))
            return
        raise AssertionError(f"no ConfigError for: {fragment}")

    def rename(cfg):
        cfg["aircraft"][0] = "a__1"
    bad(rename, "must not contain '__'")
    bad(lambda c: c.update(single_task_blocks=True), "require composite projects")
    bad(lambda c: c.update(projects={}), "no maintenance would be possible")
    bad(lambda c: c["projects"]["aircraft_1"][0].update(tasks=["nope"]), "unknown task id")
    bad(lambda c: c["projects"]["aircraft_1"][0].update(tasks=[9]), "out of range")
    bad(lambda c: c["projects"]["aircraft_1"][0].update(start=30), "in order of 'start'")
    bad(lambda c: c["projects"]["aircraft_1"][0].update(duration=-1), "non-negative")
    bad(lambda c: c["projects"].update(ghost=[]), "unknown aircraft")
    bad(lambda c: c["tasks"][1].update(id="task1"), "duplicate task id")
    bad(lambda c: c["tasks"][0].update(guard=[5, 1]), "lo > hi")
    bad(lambda c: c["tasks"][0]["interval_invariants"].pop("aircraft_2"),
        "'interval_invariants' missing")


def test_model_names():
    used = set()
    assert ntua_adapter.model_name("Aircraft-1", used) == "Aircraft_1"
    assert ntua_adapter.model_name("Aircraft_1", used) == "Aircraft_1_2"
    assert ntua_adapter.model_name("9X--Y", used) == "A_9X_Y"
    assert ntua_adapter.model_name("dot", used) == "A_dot"


# ─── NTUA example ────────────────────────────────────────────

def test_ntua_example_verdicts():
    sim, v = verdict(ntua_example())
    assert v is None and sim.time == 45

    def sched():
        return copy.deepcopy(ntua_adapter.load_schedule(
            os.path.join(EXAMPLE, "test_flp_schedule.json")))

    cases = []
    s = sched(); s[1]["T"][1] = 22
    cases.append((s, ("capacity", 22, "aircraft_2", None)))
    s = sched(); s[0]["T"][0] = 6
    cases.append((s, ("due_date", 4, "aircraft_1", "task1")))
    s = sched(); s[0]["T"][1] = 4
    cases.append((s, ("aircraft_unavailable", 4, "aircraft_1", None)))
    s = sched(); s[0]["P"][1] = ["task3"]
    cases.append((s, ("due_date", 30, "aircraft_1", "task4")))
    s = sched(); s[1]["T"][1] = 26
    cases.append((s, ("due_date", 25, "aircraft_2", "task3")))
    for s, want in cases:
        _, v = verdict(ntua_example(s))
        assert (v["type"], v["time"], v["aircraft"], v["task"]) == want, (want, v)

    # Without post-reset intervals the README schedule raises a false alarm
    cfg = ntua_example()
    for t in cfg["tasks"]:
        del t["interval_invariants"]
    _, v = verdict(cfg)
    assert (v["type"], v["time"], v["aircraft"], v["task"]) == \
        ("due_date", 7, "aircraft_2", "task1")


def test_ntua_example_files_up_to_date():
    """The shipped example outputs match what the code produces now."""
    cfg = ntua_example()
    with open(os.path.join(EXAMPLE, "test_tacpn_config.json"), encoding="utf-8") as f:
        assert json.load(f) == cfg
    gen = TACPNGenerator(cfg)
    with open(os.path.join(EXAMPLE, "test.tapn"), encoding="utf-8") as f:
        assert f.read() == gen.generate()
    net = tt.Net(gen)
    sim, _ = tt.run_schedule(net)
    for name, style in (("test.trc", "tapaal"), ("test_ntua_naming.trc", "ntua")):
        with open(os.path.join(EXAMPLE, name), encoding="utf-8") as f:
            assert f.read() == tt.trace_xml(net, sim.steps, style), name


# ─── Trace round trip and semantics ──────────────────────────

def key(v):
    if v is None:
        return None
    return (v["type"], v["time"], v.get("aircraft"), v.get("task"), v.get("step"))


def audit(cfg):
    """Independent closed-form check of the scheduling rules the net encodes.
    Returns violations sorted by (time, priority): at equal times a failed
    hangar entry (priority 0) is seen before a due date (priority 1)."""
    life, ac = cfg["lifespan"], cfg["aircraft"]
    viol, events = [], []
    for ai, a in enumerate(ac):
        for j, p in enumerate(cfg["projects"].get(a, []), 1):
            s, d = p["start"], p["duration"]
            events.append((s, 1, ai, j, "start", a))
            events.append((s + d, 0 if d > 0 else 2, ai, j, "end", a))
    events.sort(key=lambda e: e[:4])
    busy, n = set(), 0
    for t, _, _, _, kind, a in events:
        if kind == "start":
            if a in busy:
                viol.append((t, 0, "aircraft_unavailable", a, None))
                break
            if n >= cfg["hangar_count"]:
                viol.append((t, 0, "capacity", a, None))
                break
            if n >= cfg["crew_count"]:
                viol.append((t, 0, "crew", a, None))
                break
            busy.add(a)
            n += 1
        else:
            busy.discard(a)
            n -= 1
        if t > life:
            viol.append((life, 1, "horizon", None, None))
            break
    for k, task in enumerate(cfg["tasks"]):
        label = str(task.get("id", k + 1))
        for a in ac:
            r = task["timer_invariants"][a]
            iv = task.get("interval_invariants", task["timer_invariants"])[a]
            occ = [(p["start"], p["start"] + p["duration"])
                   for p in cfg["projects"].get(a, [])
                   if k in _resolve(cfg, p["tasks"])]
            if not occ:
                if r < life:
                    viol.append((r, 1, "due_date", a, label))
                continue
            if occ[0][0] > r:
                viol.append((r, 1, "due_date", a, label))
                continue
            for (_, e1), (s2, _) in zip(occ, occ[1:]):
                if s2 - e1 > iv:
                    viol.append((e1 + iv, 1, "due_date", a, label))
                    break
            else:
                if life - occ[-1][1] > iv:
                    viol.append((occ[-1][1] + iv, 1, "due_date", a, label))
    return sorted(viol, key=lambda x: (x[0], x[1]))


def test_simulator_matches_audit_and_replay():
    rng = random.Random(42)
    seen = {}
    for i in range(600):
        cfg = random_project_config(rng, intervals=(i % 3 != 0))
        net = tt.Net(TACPNGenerator(cfg))
        sim, v = tt.run_schedule(net)
        expected = audit(cfg)
        if v is None:
            assert not expected, (i, expected)
        else:
            assert expected, (i, v)
            first = expected[0]
            assert (v["time"], v["type"]) == first[:1] + first[2:3], (i, v, expected)
            same_time = [x for x in expected if x[:2] == first[:2]]
            if len(same_time) == 1:
                assert (v["aircraft"], v["task"]) == first[3:5], (i, v, first)
        seen[v["type"] if v else "feasible"] = seen.get(v["type"] if v else "feasible", 0) + 1
        for style in tt.NAMING_STYLES:
            xml = tt.trace_xml(net, sim.steps, style)
            _, v2, _ = tt.replay(net, tt.parse_trace(io.StringIO(xml)))
            assert key(v) == key(v2), (i, style, v, v2)
    for outcome in ("feasible", "capacity", "due_date", "aircraft_unavailable", "crew"):
        assert seen.get(outcome, 0) > 0, (outcome, seen)


def test_replay_rejects_bad_traces():
    cfg = ntua_example()
    net = tt.Net(TACPNGenerator(cfg))
    sim, _ = tt.run_schedule(net)
    good = tt.trace_xml(net, sim.steps)
    # wrong token age
    bad = good.replace('<token age="3" place="ComposedModel_Shared_P_timer_1__1"/>',
                       '<token age="2" place="ComposedModel_Shared_P_timer_1__1"/>', 1)
    _, v, _ = tt.replay(net, tt.parse_trace(io.StringIO(bad)))
    assert v["type"] == "task_unavailable" and v["step"] == 3, v
    # unknown transition id
    bad = good.replace("T_exit__0", "T_exit__7", 1)
    _, v, _ = tt.replay(net, tt.parse_trace(io.StringIO(bad)))
    assert v["type"] == "invalid_step", v
    # a trace that stops early is still checked up to the horizon
    head = good.split("<delay>")[0] + "</trace>\n"
    _, v, _ = tt.replay(net, tt.parse_trace(io.StringIO(head)))
    assert (v["type"], v["time"], v["aircraft"]) == ("due_date", 3, "aircraft_2"), v


# ─── CLIs and hygiene ────────────────────────────────────────

def test_clis():
    tmp = tempfile.mkdtemp(dir=HERE)
    try:
        for f in ("test.json", "test_flp_schedule.json"):
            shutil.copy(os.path.join(EXAMPLE, f), tmp)
        run = lambda *a: subprocess.run([sys.executable, "-B"] + list(a), cwd=tmp,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT,
                                        universal_newlines=True)
        r = run(os.path.join(HERE, "ntua_adapter.py"), "-d", "test.json",
                "-s", "test_flp_schedule.json", "-o", "x", "--tapn", "--verify")
        assert r.returncode == 0 and "FEASIBLE" in r.stdout, r.stdout
        with open(os.path.join(tmp, "x_tacpn_report.json"), encoding="utf-8") as f:
            rep = json.load(f)
        assert rep["verdict"] == "feasible" and rep["trace"] == "x.trc"
        r = run(os.path.join(HERE, "tacpn_trace.py"), "check", "x_tacpn_config.json", "x.trc")
        assert r.returncode == 0, r.stdout
        r = run(os.path.join(HERE, "tacpn_generator_aegean.py"), "x_tacpn_config.json")
        assert r.returncode == 0
        assert os.path.exists(os.path.join(tmp, "TACPN_2flights_4tasks.tapn"))
        with open(os.path.join(tmp, "test_flp_schedule.json"), encoding="utf-8") as f:
            s = json.load(f)
        s["Schedule"][1]["T"][1] = 22
        with open(os.path.join(tmp, "late.json"), "w", encoding="utf-8") as f:
            json.dump(s, f)
        r = run(os.path.join(HERE, "ntua_adapter.py"), "-d", "test.json",
                "-s", "late.json", "-o", "y", "--verify")
        assert r.returncode == 1 and "capacity" in r.stdout, r.stdout
    finally:
        shutil.rmtree(tmp)


def test_python38_and_quotes():
    for name in sorted(os.listdir(HERE)):
        if name.endswith(".py"):
            with open(os.path.join(HERE, name), encoding="utf-8") as f:
                src = f.read()
            ast.parse(src, feature_version=(3, 8))
            smart = [chr(c) for c in (0x2018, 0x2019, 0x201C, 0x201D)]
            assert not any(q in src for q in smart), name


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    for n, f in tests:
        f()
        print(f"ok  {n}")
    print(f"All {len(tests)} tests passed.")
