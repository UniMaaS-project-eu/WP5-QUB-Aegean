"""
TACPN trace tool: schedule -> TAPAAL trace, and trace replay
=============================================================

Verifies a maintenance schedule against the TACPN produced by
tacpn_generator_aegean.py, without the TAPAAL GUI.  TAPAAL's command-line
engines cannot replay a *timed* trace (verifydtapn has no replay option and
verifypn --trace-replay is the untimed engine), so the net semantics are
replayed here: token ages, guards, place and color invariants, hangar and
crew tokens.  The first violation is reported as (time, aircraft, task,
constraint), which is the counterexample fed back to the scheduler.

Usage:
  python tacpn_trace.py verify CONFIG [-o TRACE.trc] [--report R.json]
                                      [--naming tapaal|ntua]
      Simulates the schedule stored in CONFIG["projects"] (every project needs
      a "start"), writes the TAPAAL trace and a JSON report.  If the schedule
      is infeasible, the trace ends with the offending step (a delay past a
      due date, or a firing without the hangar/crew/aircraft token it needs):
      TAPAAL's trace import and the "check" command reject it at that step.

  python tacpn_trace.py check CONFIG TRACE.trc [--report R.json]
      Replays an existing TAPAAL trace (either naming style) against the net.
      A trace that ends before the horizon is checked for due dates that
      would expire before the horizon.

Exit codes: 0 feasible, 1 infeasible, 2 usage or config error.

Unfolded names (to be confirmed against TAPAAL on one model):
  aircraft places get the 0-based color index (P__0 = 1st aircraft, ...);
  transitions with variable 'air' are unsuffixed for the 1st aircraft and
  get __0, __1, ... for the 2nd, 3rd, ...; dot places and transitions with
  constant inscriptions have no suffix.  The prefix differs between the two
  conventions seen so far, so both are supported (--naming).
"""

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET

from tacpn_generator_aegean import TACPNGenerator, ConfigError, INF

NAMING_STYLES = {
    # Prefixes as in the March 2026 test.trc
    "tapaal": {"local": "ComposedModel_{net}_{name}",
               "shared": "ComposedModel_Shared_{name}"},
    # Prefixes as in NTUA's trace_gen_zerotimes.py
    "ntua": {"local": "ComposedModel__{name}",
             "shared": "ComposedModel__Shared__{name}"},
}
DEFAULT_NAMING = "tapaal"


# ═══════════════════════════════════════════════════════════════
# NET SEMANTICS (built from the generator, not by parsing XML)
# ═══════════════════════════════════════════════════════════════

class Net:
    def __init__(self, gen):
        self.gen = gen
        self.aircraft = list(gen.aircraft)
        self.cidx = {a: i for i, a in enumerate(self.aircraft)}
        specs = gen.place_specs()
        self.places = {s["name"]: s for s in specs}
        self.place_order = [s["name"] for s in specs]
        self.transitions = gen.transition_names()
        self.inputs = {t: [] for t in self.transitions}
        self.outputs = {t: [] for t in self.transitions}
        for src, dst, kind, guard in gen.arc_specs():
            if guard is None:
                self.outputs[src].append((dst, kind))
            else:
                self.inputs[dst].append((src, kind, guard))
        self.variable = {t: any(k == "air" for _, k, _ in self.inputs[t])
                         for t in self.transitions}
        self.aircraft_ids = gen.config.get("aircraft_ids", {})

        self.role = {"P_flying": ("flying", None), "P_bay": ("bay", None),
                     "P_crew": ("crew", None),
                     "P_ground_capacity": ("capacity", None),
                     "P_lifespan": ("lifespan", None)}
        for k in range(gen.n_tasks):
            self.role[gen.timer_name(k)] = ("timer", k)
            self.role[gen.cycle_name(k)] = ("cycle", k)
            self.role[gen.inter_name(k)] = ("inter", k)
            self.role[gen.overdue_name(k)] = ("overdue", k)
            self.role[gen.count_name(k)] = ("count", k)
        self.occ_by_transition = {}
        for occ in gen.occurrences:
            self.role[occ["inter"]] = ("project", occ)
            self.occ_by_transition[occ["maint"]] = occ
            self.occ_by_transition[occ["return"]] = occ

    @staticmethod
    def color(kind, binding):
        if kind == "air":
            return binding
        if kind in ("dot", "dot_all"):
            return "dot"
        return kind.split(":", 1)[1]

    def bound(self, place, color):
        s = self.places[place]
        b = s["plain_inv"]
        ci = s["color_inv"]
        if ci is not None:
            b = min(b, ci if s["sort"] == "dot" else ci.get(color, INF))
        return b

    def initial_marking(self):
        m = {}
        for name in self.place_order:
            mk = self.places[name]["marking"]
            if mk in ("all", "individual"):
                m[name] = [[a, 0] for a in self.aircraft]
            elif isinstance(mk, tuple):
                m[name] = [["dot", 0] for _ in range(mk[1])]
            else:
                m[name] = []
        return m

    def label(self, aircraft):
        """Original aircraft id (adapter's "aircraft_ids" map) for reports."""
        if aircraft is None or aircraft == "dot":
            return None
        return self.aircraft_ids.get(aircraft, aircraft)

    # ─── Unfolded TAPAAL names ───────────────────────────────

    def unfolded_transition(self, t, binding, style):
        base = NAMING_STYLES[style]["local"].format(net=self.gen.NET_ID, name=t)
        if self.variable[t]:
            i = self.cidx[binding]
            return base if i == 0 else f"{base}__{i - 1}"
        return base

    def unfolded_place(self, p, color, style):
        s = self.places[p]
        fmt = NAMING_STYLES[style]["shared" if s["shared"] else "local"]
        base = fmt.format(net=self.gen.NET_ID, name=p)
        return base if s["sort"] == "dot" else f"{base}__{self.cidx[color]}"

    def lookup_tables(self):
        """Unfolded name -> (transition, binding) / (place, color), all styles."""
        tmap, pmap = {}, {}
        for style in NAMING_STYLES:
            for t in self.transitions:
                for b in (self.aircraft if self.variable[t] else [None]):
                    key = self.unfolded_transition(t, b, style)
                    if tmap.get(key, (t, b)) != (t, b):
                        raise ConfigError(f"Ambiguous unfolded transition name {key}")
                    tmap[key] = (t, b)
            for p in self.place_order:
                colors = ["dot"] if self.places[p]["sort"] == "dot" else self.aircraft
                for c in colors:
                    key = self.unfolded_place(p, c, style)
                    if pmap.get(key, (p, c)) != (p, c):
                        raise ConfigError(f"Ambiguous unfolded place name {key}")
                    pmap[key] = (p, c)
        return tmap, pmap


# ═══════════════════════════════════════════════════════════════
# SIMULATOR (discrete-time TAPN semantics)
# ═══════════════════════════════════════════════════════════════

class Simulator:
    def __init__(self, net):
        self.net = net
        self.time = 0
        self.m = net.initial_marking()
        self.steps = []   # ("delay", d) | ("fire", t, binding, [(place, color, age)])

    def slack(self):
        """(time until the first invariant is reached, place, color).
        Ties prefer task timers, so a due date is reported before other limits."""
        best = None
        for p in self.net.place_order:
            timer = self.net.role[p][0] in ("timer", "cycle")
            for c, age in self.m[p]:
                key = (self.net.bound(p, c) - age, 0 if timer else 1)
                if best is None or key < best[0]:
                    best = (key, p, c)
        if best is None:
            return INF, None, None
        return best[0][0], best[1], best[2]

    def delay(self, d):
        for tokens in self.m.values():
            for tok in tokens:
                tok[1] += d
        self.time += d
        if d > 0:
            self.steps.append(("delay", d))

    def select(self, t, binding, wanted=None):
        """Tokens consumed by t: [(place, index, color, age)].
        wanted: [(place, color, age)] listed in a trace, else the oldest
        token satisfying each guard is taken.  Returns (chosen, problem)."""
        chosen, used = [], set()
        pool = list(wanted) if wanted is not None else None
        for p, kind, (lo, hi) in self.net.inputs[t]:
            c = self.net.color(kind, binding)
            if pool is not None:
                match = next((w for w in pool if w[0] == p and w[1] == c), None)
                if match is None:
                    return None, {"reason": "token_not_listed", "place": p, "color": c}
                pool.remove(match)
                age = match[2]
                if not lo <= age <= hi:
                    return None, {"reason": "guard", "place": p, "color": c,
                                  "age": age, "guard": [lo, hi]}
                idx = next((i for i, tok in enumerate(self.m[p])
                            if tok[0] == c and tok[1] == age and (p, i) not in used), None)
                if idx is None:
                    ages = sorted(tok[1] for tok in self.m[p] if tok[0] == c)
                    return None, {"reason": "missing_token", "place": p, "color": c,
                                  "age": age, "available_ages": ages}
            else:
                cands = [(tok[1], i) for i, tok in enumerate(self.m[p])
                         if tok[0] == c and (p, i) not in used]
                if not cands:
                    return None, {"reason": "missing_token", "place": p, "color": c}
                ok = [x for x in cands if lo <= x[0] <= hi]
                if not ok:
                    return None, {"reason": "guard", "place": p, "color": c,
                                  "age": max(cands)[0], "guard": [lo, hi]}
                age, idx = max(ok)
            used.add((p, idx))
            chosen.append((p, idx, c, age))
        if pool:
            return None, {"reason": "extra_tokens",
                          "tokens": [list(w) for w in pool]}
        return chosen, None

    def fire(self, t, binding, chosen):
        by_place = {}
        for p, idx, _, _ in chosen:
            by_place.setdefault(p, []).append(idx)
        for p, idxs in by_place.items():
            for i in sorted(idxs, reverse=True):
                del self.m[p][i]
        for p, kind in self.net.outputs[t]:
            self.m[p].append([self.net.color(kind, binding), 0])
        self.steps.append(("fire", t, binding, [(p, c, age) for p, _, c, age in chosen]))

    def in_maintenance(self):
        """Aircraft currently holding a hangar slot (bay or running project)."""
        busy = [c for c, _ in self.m["P_bay"]]
        for occ in self.net.gen.occurrences:
            if self.m[occ["inter"]]:
                busy.append(occ["aircraft"])
        for k in range(self.net.gen.n_tasks):
            name = self.net.gen.inter_name(k)
            if name in self.m:
                busy += [c for c, _ in self.m[name]]
        return sorted(set(busy), key=self.net.cidx.get)


# ═══════════════════════════════════════════════════════════════
# DIAGNOSIS
# ═══════════════════════════════════════════════════════════════

def _project_info(net, occ):
    if occ is None:
        return None
    return {"index": occ["index"], "start": occ["start"], "duration": occ["duration"],
            "tasks": [net.gen.task_label(k) for k in occ["tasks"]]}


def expiry_violation(net, place, color, when):
    """A token reaches its invariant at time `when` and time cannot pass."""
    role, ref = net.role[place]
    ac = net.label(color)
    v = {"time": when, "aircraft": ac, "task": None, "place": place}
    if role in ("timer", "cycle"):
        task = net.gen.task_label(ref)
        v["task"] = task
        v["type"] = "due_date"
        v["phase"] = "first due date" if role == "timer" else "after a previous execution"
        v["message"] = (f"task {task} of aircraft {ac} reaches its due date at t={when} "
                        f"without maintenance ({v['phase']})")
    elif role == "flying":
        v["type"] = "flying_limit"
        v["message"] = f"aircraft {ac} reaches its flying limit at t={when}"
    elif role == "bay":
        v["type"] = "bay_limit"
        v["message"] = f"aircraft {ac} exceeds the idle time allowed in the hangar at t={when}"
    elif role in ("inter", "project"):
        v["type"] = "duration"
        v["message"] = f"a maintenance operation exceeds its duration at t={when}"
    elif role == "lifespan":
        v["type"] = "horizon"
        v["message"] = f"the schedule continues beyond the planning horizon t={when}"
    elif role == "overdue":
        v["type"] = "overdue_limit"
        v["message"] = f"an overdue token blocks time at t={when}"
    else:
        v["type"] = "invariant"
        v["message"] = f"invariant of {place} reached at t={when}"
    return v


def firing_violation(net, sim, t, binding, problem, occ):
    """Transition t cannot fire now: translate the missing input into a constraint."""
    p = problem.get("place")
    role = net.role.get(p, (None, None))[0] if p else None
    if occ is not None:
        ac = net.label(occ["aircraft"])
    else:
        ac = net.label(binding) if binding else net.label(problem.get("color"))
    v = {"time": sim.time, "aircraft": ac, "task": None, "transition": t,
         "project": _project_info(net, occ), "detail": problem}
    reason = problem["reason"]
    if reason in ("token_not_listed", "extra_tokens"):
        v["type"] = "invalid_step"
        v["message"] = f"the tokens listed for {t} do not match its input arcs"
    elif role == "capacity":
        busy = [net.label(a) for a in sim.in_maintenance()]
        v["type"] = "capacity"
        v["message"] = (f"hangar capacity {net.gen.hangar} exceeded at t={sim.time}: "
                        f"aircraft {ac} cannot enter while {busy} are in maintenance")
    elif role == "crew":
        v["type"] = "crew"
        v["message"] = f"no maintenance crew available for aircraft {ac} at t={sim.time}"
    elif role == "flying" and reason == "missing_token":
        v["type"] = "aircraft_unavailable"
        v["message"] = (f"aircraft {ac} is not available at t={sim.time} "
                        f"(already in maintenance: overlapping projects?)")
    elif role == "flying":
        v["type"] = "entry_window"
        v["message"] = f"aircraft {ac} cannot enter the hangar at t={sim.time} (entry guard)"
    elif role in ("timer", "cycle"):
        task = net.gen.task_label(net.role[p][1])
        v["task"] = task
        if reason == "guard":
            v["type"] = "maintenance_window"
            v["message"] = (f"task {task} of aircraft {ac} is outside its maintenance "
                            f"window at t={sim.time} (age {problem['age']}, "
                            f"guard {problem['guard']})")
        else:
            v["type"] = "task_unavailable"
            v["message"] = (f"task {task} of aircraft {ac} cannot be maintained at "
                            f"t={sim.time}: its timer is not where the project expects "
                            f"it (already overdue, or projects out of order)")
    elif role in ("inter", "project"):
        v["type"] = "duration"
        v["message"] = f"{t} at t={sim.time} does not match the maintenance duration"
    elif role == "bay":
        v["type"] = "aircraft_not_in_bay"
        v["message"] = f"aircraft {ac} is not in the hangar at t={sim.time}"
    else:
        v["type"] = "invalid_step"
        v["message"] = f"{t} is not enabled at t={sim.time} ({reason} in {p})"
    return v


# ═══════════════════════════════════════════════════════════════
# SCHEDULE -> TRACE
# ═══════════════════════════════════════════════════════════════

def schedule_events(net):
    """Start/end events of every project, ordered so that at equal times the
    hangar is released before it is taken (ends before starts)."""
    events = []
    for occ in net.gen.occurrences:
        if occ["start"] is None:
            raise ConfigError(f"project {occ['maint']} has no 'start'")
        ai = net.cidx[occ["aircraft"]]
        s, d = occ["start"], occ["duration"]
        events.append((s, 1, ai, occ["index"], "start", occ))
        events.append((s + d, 0 if d > 0 else 2, ai, occ["index"], "end", occ))
    events.sort(key=lambda e: e[:4])
    return events


def advance(net, sim, target):
    """Let time pass until `target`.  If an invariant expires first, the
    requested delay is recorded as the offending (last) trace step."""
    d = target - sim.time
    if d <= 0:
        return None
    s, p, c = sim.slack()
    if d <= s:
        sim.delay(d)
        return None
    sim.steps.append(("delay", d))
    v = expiry_violation(net, p, c, sim.time + s)
    v["step"] = len(sim.steps)
    return v


def offending_tokens(sim, t, binding):
    """Token list for a transition that cannot fire: the tokens it would take,
    with a placeholder age (the guard's lower bound) for missing ones, so that
    a replay fails on the same input arc."""
    toks, used = [], set()
    for p, kind, (lo, hi) in sim.net.inputs[t]:
        c = sim.net.color(kind, binding)
        cands = [(tok[1], i) for i, tok in enumerate(sim.m[p])
                 if tok[0] == c and (p, i) not in used]
        ok = [x for x in cands if lo <= x[0] <= hi]
        pick = max(ok) if ok else (max(cands) if cands else None)
        if pick is None:
            toks.append((p, c, lo))
        else:
            used.add((p, pick[1]))
            toks.append((p, c, pick[0]))
    return toks


def run_schedule(net):
    """Translate the schedule into a trace by simulating it.  The trace ends
    with the first offending step, if any (TAPAAL rejects it at that step)."""
    sim = Simulator(net)
    for time, _, _, _, kind, occ in schedule_events(net):
        v = advance(net, sim, time)
        if v is not None:
            return sim, v
        a = occ["aircraft"]
        if kind == "start":
            seq = [("T_enter", a), (occ["maint"], None)]
        else:
            seq = [(occ["return"], None), ("T_exit", a)]
        for t, b in seq:
            chosen, problem = sim.select(t, b)
            if problem is not None:
                v = firing_violation(net, sim, t, b, problem, occ)
                sim.steps.append(("fire", t, b, offending_tokens(sim, t, b)))
                v["step"] = len(sim.steps)
                return sim, v
            sim.fire(t, b, chosen)
    return sim, advance(net, sim, net.gen.lifespan)


def trace_xml(net, steps, style=DEFAULT_NAMING):
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="no"?>', '<trace>']
    for st in steps:
        if st[0] == "delay":
            lines.append(f'  <delay>{st[1]}</delay>')
        else:
            _, t, b, consumed = st
            lines.append(f'  <transition id="{net.unfolded_transition(t, b, style)}">')
            for p, c, age in consumed:
                lines.append(f'    <token age="{age}" '
                             f'place="{net.unfolded_place(p, c, style)}"/>')
            lines.append('  </transition>')
    lines.append('</trace>')
    return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════════
# TRACE REPLAY
# ═══════════════════════════════════════════════════════════════

def _number(text, what):
    value = float(text)
    if value != int(value) or value < 0:
        raise ValueError(f"{what} {text!r} is not a non-negative integer "
                         f"(discrete time expected)")
    return int(value)


def parse_trace(path):
    """[("delay", d) | ("fire", id, [(place_id, age)]) | ("other", tag)]."""
    root = ET.parse(path).getroot()
    steps = []
    for el in root:
        tag = el.tag.split("}")[-1]
        if tag == "delay":
            steps.append(("delay", _number(el.text.strip(), "delay")))
        elif tag == "transition":
            toks = [(tok.get("place"), _number(tok.get("age"), "token age"))
                    for tok in el if tok.tag.split("}")[-1] == "token"]
            steps.append(("fire", el.get("id"), toks))
        else:
            steps.append(("other", tag))
    return steps


def replay(net, steps):
    tmap, pmap = net.lookup_tables()
    sim = Simulator(net)
    notes = []
    for i, st in enumerate(steps, start=1):
        if st[0] == "delay":
            s, p, c = sim.slack()
            if st[1] > s:
                v = expiry_violation(net, p, c, sim.time + s)
                v["step"] = i
                v["message"] += f" (trace asks for a delay of {st[1]} at t={sim.time})"
                return sim, v, notes
            sim.delay(st[1])
        elif st[0] == "fire":
            if st[1] not in tmap:
                return sim, {"type": "invalid_step", "step": i, "time": sim.time,
                             "message": f"unknown transition id {st[1]}"}, notes
            t, b = tmap[st[1]]
            wanted = []
            for pid, age in st[2]:
                if pid not in pmap:
                    return sim, {"type": "invalid_step", "step": i, "time": sim.time,
                                 "message": f"unknown place id {pid}"}, notes
                p, c = pmap[pid]
                wanted.append((p, c, age))
            chosen, problem = sim.select(t, b, wanted)
            if problem is not None:
                v = firing_violation(net, sim, t, b, problem,
                                     net.occ_by_transition.get(t))
                v["step"] = i
                return sim, v, notes
            sim.fire(t, b, chosen)
            if t.startswith("T_overdue"):
                p, c, _ = sim.steps[-1][3][0]
                v = expiry_violation(net, p, c, sim.time)
                v["step"] = i
                v["message"] = "the trace fires " + t + ": " + v["message"]
                return sim, v, notes
        else:
            notes.append(f"step {i}: ignored <{st[1]}> element")
    if sim.time < net.gen.lifespan:
        s, p, c = sim.slack()
        if net.gen.lifespan - sim.time > s:
            v = expiry_violation(net, p, c, sim.time + s)
            v["message"] += f" (after the last trace step at t={sim.time})"
            return sim, v, notes
        notes.append(f"trace ends at t={sim.time}; no due date expires before the "
                     f"horizon t={net.gen.lifespan}")
    return sim, None, notes


# ═══════════════════════════════════════════════════════════════
# REPORT / CLI
# ═══════════════════════════════════════════════════════════════

def make_report(net, sim, violation, mode, **extra):
    rep = {
        "mode": mode,
        "verdict": "feasible" if violation is None else "infeasible",
        "horizon": net.gen.lifespan,
        "end_time": sim.time,
        "aircraft": len(net.aircraft),
        "tasks": net.gen.n_tasks,
        "projects": len(net.gen.occurrences),
        "hangar_capacity": net.gen.hangar,
        "violation": violation,
    }
    rep.update(extra)
    return rep


def _stem(path):
    return os.path.splitext(os.path.abspath(path))[0]


def _relative(path, report_path):
    """Path as seen from the report's folder (no absolute local paths in reports)."""
    try:
        return os.path.relpath(os.path.abspath(path),
                               os.path.dirname(os.path.abspath(report_path)))
    except ValueError:   # different drives on Windows
        return os.path.basename(path)


def _load(config_path):
    with open(config_path, encoding="utf-8") as f:
        return Net(TACPNGenerator(json.load(f)))


def cmd_verify(args):
    net = _load(args.config)
    sim, v = run_schedule(net)
    trace_path = args.output or _stem(args.config) + ".trc"
    report_path = args.report or _stem(args.config) + "_report.json"
    with open(trace_path, "w", encoding="utf-8") as f:
        f.write(trace_xml(net, sim.steps, args.naming))
    rep = make_report(net, sim, v, "verify", trace=_relative(trace_path, report_path),
                      naming=args.naming)
    return rep, report_path, trace_path


def cmd_check(args):
    net = _load(args.config)
    sim, v, notes = replay(net, parse_trace(args.trace))
    report_path = args.report or _stem(args.trace) + "_report.json"
    rep = make_report(net, sim, v, "check", trace=_relative(args.trace, report_path),
                      notes=notes)
    return rep, report_path, args.trace


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build and replay TAPAAL traces for the generated TACPN.")
    sub = parser.add_subparsers(dest="cmd")
    pv = sub.add_parser("verify", help="simulate the schedule in CONFIG, write the "
                                       "trace and a JSON report")
    pv.add_argument("config")
    pv.add_argument("-o", "--output", help="trace file (default: <config>.trc)")
    pv.add_argument("--report", help="report file (default: <config>_report.json)")
    pv.add_argument("--naming", choices=sorted(NAMING_STYLES), default=DEFAULT_NAMING,
                    help="unfolded-name prefix convention (default: %(default)s)")
    pc = sub.add_parser("check", help="replay an existing TAPAAL trace")
    pc.add_argument("config")
    pc.add_argument("trace")
    pc.add_argument("--report", help="report file (default: <trace>_report.json)")
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 2
    try:
        if args.cmd == "verify":
            rep, report_path, trace_path = cmd_verify(args)
        else:
            rep, report_path, trace_path = cmd_check(args)
    except (ConfigError, ValueError, OSError, ET.ParseError) as e:
        print(f"Error: {e}")
        return 2
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
    print(f"Verdict: {rep['verdict'].upper()}  (horizon {rep['horizon']}, "
          f"trace ends at t={rep['end_time']})")
    if rep["violation"]:
        v = rep["violation"]
        step = f" [trace step {v['step']}]" if v.get("step") else ""
        print(f"  {v['type']}{step}: {v['message']}")
    print(f"Report: {os.path.abspath(report_path)}")
    if args.cmd == "verify":
        print(f"Trace:  {os.path.abspath(trace_path)}")
    return 0 if rep["verdict"] == "feasible" else 1


if __name__ == "__main__":
    sys.exit(main())
