"""
TACPN TAPAAL XML Generator for Aircraft Maintenance Scheduling
==============================================================

Generates a Timed-Arc Colored Petri Net (TACPN) model in TAPAAL's .tapn XML
format for aircraft base maintenance scheduling verification.

Model architecture (per task k):
  - Timer place       P_timer_k   : time since last task-k maintenance
  - Maint transition  T_maint_k   : performs the task (guarded by time window)
  - Intermediate      P_inter_k   : aircraft under task k (invariant = task duration)
  - Return transition T_return_k  : resets timer, releases crew, back to bay
  - Overdue trans.    T_overdue_k (uniform deadlines) or
                      T_overdue_k_<A> (one per aircraft, heterogeneous deadlines)
  - Overdue place     P_overdue_k : collects violations (checked by the query)
  - Count place       P_count_k   : counts task completions

Composite projects (v3, optional "projects" key), per aircraft A and project j:
  - T_maint_<A>_P<j>  : takes the timers of all tasks of the project at once
  - P_inter_<A>_P<j>  : holds the aircraft for exactly the project duration
  - T_return_<A>_P<j> : [d,d] arc, then resets all project timers together
  - P_cycle_k         : post-reset timer place (optional "interval_invariants")

Global infrastructure:
  - P_flying, P_bay, T_enter, T_exit, P_crew, P_ground_capacity, P_lifespan

Usage:
  python tacpn_generator_aegean.py                        # reads example_config.json
  python tacpn_generator_aegean.py config.json            # reads the given JSON file
  python tacpn_generator_aegean.py config.json -o m.tapn  # explicit output file

Changes in v2:
  * Default intermediate / bay invariants now reproduce the reference model
    (intermediate = max(1, (hi-lo)//4), bay = 3) and can be overridden.
  * Heterogeneous per-aircraft deadlines: the overdue transition is split per
    aircraft, so every aircraft can actually reach its own overdue guard
    (the v1 shared guard [max_inv, inf) caused timelocks that hid violations).
  * Uniform deadlines use plain place invariants, exactly as in the reference.
  * Config validation with explicit error messages.

Changes in v3:
  * Composite projects (optional "projects"): a project of several tasks is one
    maintenance operation with an exact duration [d,d]; its task timers are
    taken together at the start and reset together at the end.
  * Post-reset deadlines (optional "interval_invariants"): the first deadline
    (remaining life) and the deadline after a reset (full interval) can differ.
  * Optional task labels ("id"); projects may reference tasks by id or index.
  * Overdue transitions that could only fire at or after the horizon are omitted.
  * Config paths are resolved from the current directory (pipeline use);
    output goes next to the config unless -o is given.
  * Configs without the new keys produce exactly the v2 output.
"""

import argparse
import json
import os
import re
import sys
import math

INF = float("inf")


# ═══════════════════════════════════════════════════════════════
# XML SNIPPET HELPERS
# ═══════════════════════════════════════════════════════════════

def indent(text, level):
    prefix = "  " * level
    return "\n".join(prefix + line if line.strip() else "" for line in text.split("\n"))


def inv_str(value):
    """Invariant attribute string: '<= v' or '< inf'."""
    return "&lt; inf" if value == INF else f"&lt;= {value}"


def xml_type_block(sort_name):
    return (
        f'<type>\n'
        f'  <text>{sort_name}</text>\n'
        f'  <structure>\n'
        f'    <usersort declaration="{sort_name}"/>\n'
        f'  </structure>\n'
        f'</type>'
    )


def _numberof(count, inner):
    return (
        f'<numberof>\n'
        f'  <subterm>\n'
        f'    <numberconstant value="{count}">\n'
        f'      <positive/>\n'
        f'    </numberconstant>\n'
        f'  </subterm>\n'
        f'  <subterm>\n'
        f'{indent(inner, 2)}\n'
        f'  </subterm>\n'
        f'</numberof>'
    )


def xml_hlinitialmarking_aircraft_all(count=1):
    body = _numberof(count, '<all>\n  <usersort declaration="aircraft"/>\n</all>')
    return (
        f'<hlinitialMarking>\n'
        f'  <text>{count}\'aircraft.all</text>\n'
        f'  <structure>\n'
        f'    <add>\n'
        f'      <subterm>\n'
        f'{indent(body, 4)}\n'
        f'      </subterm>\n'
        f'    </add>\n'
        f'  </structure>\n'
        f'</hlinitialMarking>'
    )


def xml_hlinitialmarking_individual(aircraft_names):
    text = " + ".join(f"1'{a}" for a in aircraft_names)
    blocks = []
    for a in aircraft_names:
        inner = '<useroperator declaration="' + a + '"/>'
        body = indent(_numberof(1, inner), 4)
        blocks.append('      <subterm>\n' + body + '\n      </subterm>')
    subterms = "\n".join(blocks)
    return (
        f'<hlinitialMarking>\n'
        f'  <text>{text}</text>\n'
        f'  <structure>\n'
        f'    <add>\n'
        f'{subterms}\n'
        f'    </add>\n'
        f'  </structure>\n'
        f'</hlinitialMarking>'
    )


def xml_hlinitialmarking_dot(count):
    body = _numberof(count, '<useroperator declaration="dot"/>')
    return (
        f'<hlinitialMarking>\n'
        f'  <text>{count}\'dot</text>\n'
        f'  <structure>\n'
        f'    <add>\n'
        f'      <subterm>\n'
        f'{indent(body, 4)}\n'
        f'      </subterm>\n'
        f'    </add>\n'
        f'  </structure>\n'
        f'</hlinitialMarking>'
    )


def xml_colorinvariant(inv_value, sort_name, color_value):
    return (
        f'<colorinvariant>\n'
        f'  <inscription inscription="&lt;= {inv_value}"/>\n'
        f'  <colortype name="{sort_name}">\n'
        f'    <color value="{color_value}"/>\n'
        f'  </colortype>\n'
        f'</colorinvariant>'
    )


def xml_hlinscription(kind):
    """
    Arc HL inscription.
      'air'      -> 1'air        (variable)
      'dot'      -> 1'dot
      'dot_all'  -> 1'dot.all
      'const:X'  -> 1'X          (specific aircraft constant X)
    """
    if kind == "air":
        text, inner = "1'air", '<variable refvariable="air"/>'
    elif kind == "dot":
        text, inner = "1'dot", '<useroperator declaration="dot"/>'
    elif kind == "dot_all":
        text, inner = "1'dot.all", '<all>\n  <usersort declaration="dot"/>\n</all>'
    elif kind.startswith("const:"):
        c = kind.split(":", 1)[1]
        text, inner = f"1'{c}", f'<useroperator declaration="{c}"/>'
    else:
        raise ValueError(f"Unknown inscription kind: {kind}")
    return (
        f'<hlinscription>\n'
        f'  <text>{text}</text>\n'
        f'  <structure>\n'
        f'{indent(_numberof(1, inner), 2)}\n'
        f'  </structure>\n'
        f'</hlinscription>'
    )


# ═══════════════════════════════════════════════════════════════
# CONFIG VALIDATION
# ═══════════════════════════════════════════════════════════════

class ConfigError(ValueError):
    pass


def _pos_int(value, name):
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"'{name}' must be a positive integer, got {value!r}")


def _nonneg_int(value, name):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ConfigError(f"'{name}' must be a non-negative integer, got {value!r}")


def _task_ref_index(ref, tasks, where):
    """Resolve a project task reference (1-based index or task id) to an index."""
    if isinstance(ref, bool):
        raise ConfigError(f"{where}: invalid task reference {ref!r}")
    if isinstance(ref, int):
        if not 1 <= ref <= len(tasks):
            raise ConfigError(f"{where}: task index {ref} out of range 1..{len(tasks)}")
        return ref - 1
    if isinstance(ref, str):
        for k, task in enumerate(tasks):
            if "id" in task and str(task["id"]) == ref:
                return k
        raise ConfigError(f"{where}: unknown task id {ref!r}")
    raise ConfigError(f"{where}: invalid task reference {ref!r}")


def validate_config(config):
    """Raise ConfigError with a clear message if the config is inconsistent."""
    for key in ["aircraft", "flying_invariants", "tasks",
                "crew_count", "hangar_count", "lifespan"]:
        if key not in config:
            raise ConfigError(f"Missing required key '{key}'")

    aircraft = config["aircraft"]
    if not isinstance(aircraft, list) or not aircraft:
        raise ConfigError("'aircraft' must be a non-empty list")
    if len(set(aircraft)) != len(aircraft):
        raise ConfigError("'aircraft' contains duplicate names")
    reserved = {"dot", "air", "aircraft"}
    for a in aircraft:
        if not isinstance(a, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", a):
            raise ConfigError(f"Aircraft name {a!r} must start with a letter and "
                              f"contain only letters, digits or '_'")
        if a in reserved:
            raise ConfigError(f"Aircraft name {a!r} is reserved")
        if "__" in a:
            raise ConfigError(f"Aircraft name {a!r} must not contain '__' "
                              f"(reserved for TAPAAL's unfolded names)")

    fly = config["flying_invariants"]
    missing = [a for a in aircraft if a not in fly]
    if missing:
        raise ConfigError(f"'flying_invariants' is missing aircraft: {missing}")
    for a in aircraft:
        _pos_int(fly[a], f"flying_invariants[{a}]")

    if "entry_guard" in config:
        lo, hi = config["entry_guard"]
        _nonneg_int(lo, "entry_guard[0]")
        _nonneg_int(hi, "entry_guard[1]")
        if lo > hi:
            raise ConfigError(f"entry_guard {config['entry_guard']}: lo > hi")
        too_small = [a for a in aircraft if fly[a] < lo]
        if too_small:
            raise ConfigError(f"entry_guard lower bound {lo} exceeds the flying "
                              f"invariant of {too_small}: they could never enter "
                              f"maintenance")

    tasks = config["tasks"]
    if not isinstance(tasks, list) or not tasks:
        raise ConfigError("'tasks' must be a non-empty list")
    seen_ids = set()
    for k, task in enumerate(tasks, start=1):
        if "guard" not in task or "timer_invariants" not in task:
            raise ConfigError(f"Task {k}: needs 'guard' and 'timer_invariants'")
        if "id" in task:
            tid = task["id"]
            if isinstance(tid, bool) or not isinstance(tid, (str, int)):
                raise ConfigError(f"Task {k}: 'id' must be a string, got {tid!r}")
            if str(tid) in seen_ids:
                raise ConfigError(f"Task {k}: duplicate task id {tid!r}")
            seen_ids.add(str(tid))
        lo, hi = task["guard"]
        _nonneg_int(lo, f"task {k} guard[0]")
        _nonneg_int(hi, f"task {k} guard[1]")
        if lo > hi:
            raise ConfigError(f"Task {k}: guard {task['guard']} has lo > hi")
        tinv = task["timer_invariants"]
        missing = [a for a in aircraft if a not in tinv]
        if missing:
            raise ConfigError(f"Task {k}: 'timer_invariants' missing aircraft {missing}")
        for a in aircraft:
            _nonneg_int(tinv[a], f"task {k} timer_invariants[{a}]")
        blocked = [a for a in aircraft if tinv[a] < lo]
        if blocked:
            raise ConfigError(
                f"Task {k}: guard lower bound {lo} exceeds the timer invariant of "
                f"{blocked}; those aircraft could never be maintained (timelock)")
        if "interval_invariants" in task:
            iv = task["interval_invariants"]
            missing = [a for a in aircraft if a not in iv]
            if missing:
                raise ConfigError(f"Task {k}: 'interval_invariants' missing aircraft {missing}")
            for a in aircraft:
                _nonneg_int(iv[a], f"task {k} interval_invariants[{a}]")
        if "duration" in task:
            _pos_int(task["duration"], f"task {k} duration")
        if "overdue_invariant" in task:
            _pos_int(task["overdue_invariant"], f"task {k} overdue_invariant")

    _pos_int(config["crew_count"], "crew_count")
    _pos_int(config["hangar_count"], "hangar_count")
    _pos_int(config["lifespan"], "lifespan")
    if "bay_invariant" in config:
        _nonneg_int(config["bay_invariant"], "bay_invariant")

    projects = config.get("projects", {})
    if not isinstance(projects, dict):
        raise ConfigError("'projects' must map aircraft names to lists of projects")
    for a, plist in projects.items():
        if a not in aircraft:
            raise ConfigError(f"'projects' refers to unknown aircraft {a!r}")
        if not isinstance(plist, list):
            raise ConfigError(f"projects[{a}] must be a list")
        starts = []
        for j, proj in enumerate(plist, start=1):
            where = f"projects[{a}][{j}]"
            if not isinstance(proj, dict) or "tasks" not in proj or "duration" not in proj:
                raise ConfigError(f"{where}: needs 'tasks' and 'duration'")
            if not isinstance(proj["tasks"], list) or not proj["tasks"]:
                raise ConfigError(f"{where}: 'tasks' must be a non-empty list")
            for ref in proj["tasks"]:
                _task_ref_index(ref, tasks, where)
            _nonneg_int(proj["duration"], f"{where} duration")
            if "start" in proj:
                _nonneg_int(proj["start"], f"{where} start")
                starts.append(proj["start"])
        if len(starts) == len(plist) and starts != sorted(starts):
            raise ConfigError(f"projects[{a}] must be listed in order of 'start'")

    single = config.get("single_task_blocks", not projects)
    if not isinstance(single, bool):
        raise ConfigError("'single_task_blocks' must be true or false")
    if not single and not any(projects.values()):
        raise ConfigError("'single_task_blocks' is false but no 'projects' are "
                          "given: no maintenance would be possible")
    if single and any("interval_invariants" in t for t in tasks):
        raise ConfigError("'interval_invariants' require composite projects with "
                          "'single_task_blocks': false")


# ═══════════════════════════════════════════════════════════════
# MAIN GENERATOR
# ═══════════════════════════════════════════════════════════════

class TACPNGenerator:
    """
    Required config keys:
      aircraft, flying_invariants, tasks[{guard, timer_invariants}],
      crew_count, hangar_count, lifespan
    Optional config keys (defaults reproduce the reference model):
      entry_guard       [lo, hi]  default [max(1, floor(0.4*min_fly)), max_fly]
      bay_invariant     int       default 3
      tasks[k].duration int       intermediate-place invariant,
                                  default max(1, (hi-lo)//4)
      tasks[k].overdue_invariant  default max(50, int(1.75*max_timer_inv))
    Optional v3 keys:
      tasks[k].id                   label used by projects and reports
      tasks[k].interval_invariants  {aircraft: deadline after a reset}
      projects            {aircraft: [{tasks: [id or index], duration, start?}]}
      single_task_blocks  bool, default true without projects, false with them
    """

    DEFAULT_BAY_INVARIANT = 3
    NET_ID = "TAPN1"

    def __init__(self, config):
        validate_config(config)
        self.config = config
        self.aircraft = config["aircraft"]
        self.n_ac = len(self.aircraft)
        self.tasks = [dict(t) for t in config["tasks"]]   # do not mutate input
        self.n_tasks = len(self.tasks)
        self.flying_inv = config["flying_invariants"]
        self.crew = config["crew_count"]
        self.hangar = config["hangar_count"]
        self.lifespan = config["lifespan"]
        self.bay_inv = config.get("bay_invariant", self.DEFAULT_BAY_INVARIANT)
        self.single_blocks = config.get("single_task_blocks",
                                        not config.get("projects"))

        self.arc_counter = 0
        self._compute_derived()
        self._compute_layout()

    # ─── Derived parameters ──────────────────────────────────

    def _compute_derived(self):
        fly = [self.flying_inv[a] for a in self.aircraft]
        if "entry_guard" in self.config:
            self.entry_guard_lo, self.entry_guard_hi = self.config["entry_guard"]
        else:
            self.entry_guard_lo = max(1, int(math.floor(min(fly) * 0.4)))
            self.entry_guard_hi = max(fly)

        for task in self.tasks:
            lo, hi = task["guard"]
            inv = task["timer_invariants"]
            max_inv = max(inv[a] for a in self.aircraft)
            task["_inter_inv"] = task.get("duration", max(1, (hi - lo) // 4))
            task["_overdue_inv"] = task.get("overdue_invariant",
                                            max(50, int(max_inv * 1.75)))
            task["_uniform"] = len({inv[a] for a in self.aircraft}) == 1
            task["_uniform_inv"] = max_inv if task["_uniform"] else None
            iv = task.get("interval_invariants")
            task["_has_cycle"] = iv is not None
            if iv is not None:
                max_iv = max(iv[a] for a in self.aircraft)
                task["_cycle_uniform"] = len({iv[a] for a in self.aircraft}) == 1
                task["_cycle_uniform_inv"] = max_iv if task["_cycle_uniform"] else None
                task["_cycle_guard"] = (0, max_iv)

        # Composite project occurrences, in aircraft order then listed order.
        # The first occurrence of a task takes its timer from P_timer_k; later
        # occurrences take it from P_cycle_k (if the task has a post-reset interval).
        self.occurrences = []
        projects = self.config.get("projects", {})
        for a in self.aircraft:
            seen = set()
            for j, proj in enumerate(projects.get(a, []), start=1):
                ks = []
                for ref in proj["tasks"]:
                    k = _task_ref_index(ref, self.tasks, f"projects[{a}][{j}]")
                    if k not in ks:
                        ks.append(k)
                sources = {k: ("cycle" if k in seen and self.tasks[k]["_has_cycle"]
                               else "timer") for k in ks}
                seen.update(ks)
                self.occurrences.append({
                    "aircraft": a, "index": j, "tasks": ks,
                    "duration": proj["duration"], "start": proj.get("start"),
                    "sources": sources,
                    "maint": f"T_maint_{a}_P{j}", "return": f"T_return_{a}_P{j}",
                    "inter": f"P_inter_{a}_P{j}",
                })

    # ─── Names ───────────────────────────────────────────────

    def timer_name(self, k):   return f"P_timer_{k+1}"
    def cycle_name(self, k):   return f"P_cycle_{k+1}"
    def inter_name(self, k):   return f"P_inter_{k+1}"
    def overdue_name(self, k): return f"P_overdue_{k+1}"
    def count_name(self, k):   return f"P_count_{k+1}"
    def maint_name(self, k):   return f"T_maint_{k+1}"
    def return_name(self, k):  return f"T_return_{k+1}"

    def task_label(self, k):
        """Task id if given, else its 1-based index (for reports)."""
        return str(self.tasks[k].get("id", k + 1))

    def overdue_t_names(self, k):
        """[(transition_name, aircraft_or_None)] for task k.

        Overdue transitions whose guard starts at or after the horizon are
        omitted: they could never fire before the planning horizon ends.
        """
        t = self.tasks[k]
        if t["_uniform"]:
            if t["_uniform_inv"] >= self.lifespan:
                return []
            return [(f"T_overdue_{k+1}", None)]
        return [(f"T_overdue_{k+1}_{a}", a) for a in self.aircraft
                if t["timer_invariants"][a] < self.lifespan]

    def cycle_overdue_t_names(self, k):
        """[(transition_name, aircraft_or_None)] for the post-reset place of task k."""
        t = self.tasks[k]
        if not t["_has_cycle"]:
            return []
        if t["_cycle_uniform"]:
            if t["_cycle_uniform_inv"] >= self.lifespan:
                return []
            return [(f"T_overdue_cycle_{k+1}", None)]
        return [(f"T_overdue_cycle_{k+1}_{a}", a) for a in self.aircraft
                if t["interval_invariants"][a] < self.lifespan]

    # ─── Layout ──────────────────────────────────────────────

    def _compute_layout(self):
        self.pos = {}
        gy = 400
        self.pos["P_flying"] = (105, gy)
        self.pos["P_ground_capacity"] = (315, gy)
        self.pos["T_enter"] = (405, gy - 105)
        self.pos["T_exit"] = (405, gy + 105)
        self.pos["P_bay"] = (540, gy)
        self.pos["P_crew"] = (1335, gy)
        self.pos["P_lifespan"] = (255, gy + 210)

        for k in range(self.n_tasks):
            ty = gy - 210 if k == 0 else gy + 60 + k * 360
            self.pos[self.timer_name(k)] = (615 + k * 30, ty)
            self.pos[self.maint_name(k)] = (765, ty)
            self.pos[self.overdue_name(k)] = (1140 + k * 30, ty)
            self.pos[self.count_name(k)] = (945 - k * 60, ty - 15)
            self.pos[self.inter_name(k)] = (765, ty + 120 if k == 0 else ty - 90)
            self.pos[self.return_name(k)] = (765, ty + 195 if k == 0 else ty - 160)
            for i, (tname, _) in enumerate(self.overdue_t_names(k)):
                self.pos[tname] = (990 + k * 15, ty + i * 45)
            if self.tasks[k]["_has_cycle"]:
                self.pos[self.cycle_name(k)] = (615 + k * 30, ty + 75)
                for i, (tname, _) in enumerate(self.cycle_overdue_t_names(k)):
                    self.pos[tname] = (1065 + k * 15, ty + 30 + i * 45)

        for occ in self.occurrences:
            ai = self.aircraft.index(occ["aircraft"])
            x = 1530 + 240 * (occ["index"] - 1)
            y = 120 + 330 * ai
            self.pos[occ["maint"]] = (x, y)
            self.pos[occ["inter"]] = (x, y + 105)
            self.pos[occ["return"]] = (x, y + 210)

    def _center(self, name):
        x, y = self.pos.get(name, (400, 400))
        return (x + 15, y + 15)

    def _next_arc_id(self):
        aid = f"A{self.arc_counter}"
        self.arc_counter += 1
        return aid

    # ─── Declaration ─────────────────────────────────────────

    def _declaration(self):
        consts = "\n".join(
            f'            <feconstant id="{a}" name="aircraft"/>' for a in self.aircraft)
        return (
            f'  <declaration>\n'
            f'    <structure>\n'
            f'      <declarations>\n'
            f'        <namedsort id="dot" name="dot">\n'
            f'          <dot/>\n'
            f'        </namedsort>\n'
            f'        <namedsort id="aircraft" name="aircraft">\n'
            f'          <cyclicenumeration>\n'
            f'{consts}\n'
            f'          </cyclicenumeration>\n'
            f'        </namedsort>\n'
            f'        <variabledecl id="air" name="air">\n'
            f'          <usersort declaration="aircraft"/>\n'
            f'        </variabledecl>\n'
            f'      </declarations>\n'
            f'    </structure>\n'
            f'  </declaration>'
        )

    # ─── Invariant helpers ───────────────────────────────────

    def _timer_invariant_parts(self, k):
        """(plain invariant, color-invariant dict or None) for the timer place."""
        t = self.tasks[k]
        if t["_uniform"]:
            return t["_uniform_inv"], None
        return INF, t["timer_invariants"]

    def _cycle_invariant_parts(self, k):
        """(plain invariant, color-invariant dict or None) for the post-reset place."""
        t = self.tasks[k]
        if t["_cycle_uniform"]:
            return t["_cycle_uniform_inv"], None
        return INF, t["interval_invariants"]

    # ─── Shared places ───────────────────────────────────────

    def _shared_aircraft(self, name, plain_inv, color_inv, individual_marking):
        """individual_marking: True (1'A1+...), False (1'aircraft.all), None (empty)."""
        count = 0 if individual_marking is None else self.n_ac
        lines = [f'  <shared-place initialMarking="{count}" '
                 f'invariant="{inv_str(plain_inv)}" name="{name}">']
        if color_inv:
            for a in self.aircraft:
                lines.append(indent(xml_colorinvariant(color_inv[a], "aircraft", a), 2))
        lines.append(indent(xml_type_block("aircraft"), 2))
        if individual_marking is True:
            lines.append(indent(xml_hlinitialmarking_individual(self.aircraft), 2))
        elif individual_marking is False:
            lines.append(indent(xml_hlinitialmarking_aircraft_all(1), 2))
        lines.append('  </shared-place>')
        return "\n".join(lines)

    def _shared_dot(self, name, count):
        return "\n".join([
            f'  <shared-place initialMarking="{count}" invariant="&lt; inf" name="{name}">',
            indent(xml_type_block("dot"), 2),
            indent(xml_hlinitialmarking_dot(count), 2),
            '  </shared-place>',
        ])

    def _all_shared_places(self):
        parts = []
        for k in range(self.n_tasks):
            plain, color = self._timer_invariant_parts(k)
            parts.append(self._shared_aircraft(self.timer_name(k), plain, color, False))
        for k in range(self.n_tasks):
            if self.tasks[k]["_has_cycle"]:
                plain, color = self._cycle_invariant_parts(k)
                parts.append(self._shared_aircraft(self.cycle_name(k), plain, color, None))
        parts.append(self._shared_aircraft("P_flying", INF, self.flying_inv, True))
        parts.append(self._shared_dot("P_ground_capacity", self.hangar))
        parts.append(self._shared_dot("P_crew", self.crew))
        return "\n".join(parts)

    # ─── Structure (single source for the XML and for tacpn_trace.py) ──

    def place_specs(self):
        """Net places, in output order, as dicts:
        name, sort, initial, plain_inv, color_inv, marking, shared."""
        def spec(name, sort, initial, plain_inv, color_inv=None, marking=None,
                 shared=False):
            return {"name": name, "sort": sort, "initial": initial,
                    "plain_inv": plain_inv, "color_inv": color_inv,
                    "marking": marking, "shared": shared}
        p = [
            spec("P_flying", "aircraft", self.n_ac, INF, self.flying_inv,
                 "individual", shared=True),
            spec("P_bay", "aircraft", 0, self.bay_inv),
            spec("P_crew", "dot", self.crew, INF, marking=("dot", self.crew),
                 shared=True),
            spec("P_ground_capacity", "dot", self.hangar, INF,
                 marking=("dot", self.hangar), shared=True),
            spec("P_lifespan", "dot", 1, INF, color_inv=self.lifespan,
                 marking=("dot", 1)),
        ]
        for k, t in enumerate(self.tasks):
            plain, color = self._timer_invariant_parts(k)
            p.append(spec(self.timer_name(k), "aircraft", self.n_ac, plain, color,
                          "all", shared=True))
            if self.single_blocks:
                p.append(spec(self.inter_name(k), "aircraft", 0, t["_inter_inv"]))
            p.append(spec(self.overdue_name(k), "aircraft", 0, t["_overdue_inv"]))
            p.append(spec(self.count_name(k), "aircraft", 0, INF))
            if t["_has_cycle"]:
                plain, color = self._cycle_invariant_parts(k)
                p.append(spec(self.cycle_name(k), "aircraft", 0, plain, color,
                              shared=True))
        for occ in self.occurrences:
            p.append(spec(occ["inter"], "dot", 0, occ["duration"]))
        return p

    def transition_names(self):
        names = ["T_enter", "T_exit"]
        for k in range(self.n_tasks):
            if self.single_blocks:
                names += [self.maint_name(k), self.return_name(k)]
            names += [n for n, _ in self.overdue_t_names(k)]
            names += [n for n, _ in self.cycle_overdue_t_names(k)]
        for occ in self.occurrences:
            names += [occ["maint"], occ["return"]]
        return names

    def arc_specs(self):
        """Arcs in output order: (source, target, kind, guard).
        guard=None -> output arc; guard=(lo, hi) -> timed input arc."""
        a = []
        # Global entry / exit
        a.append(("P_flying", "T_enter", "air",
                  (self.entry_guard_lo, self.entry_guard_hi)))
        a.append(("P_ground_capacity", "T_enter", "dot", (0, INF)))
        a.append(("T_enter", "P_bay", "air", None))
        a.append(("P_bay", "T_exit", "air", (0, INF)))
        a.append(("T_exit", "P_flying", "air", None))
        a.append(("T_exit", "P_ground_capacity", "dot", None))

        for k, t in enumerate(self.tasks):
            lo, hi = t["guard"]
            tm, tr = self.maint_name(k), self.return_name(k)
            pt, pi = self.timer_name(k), self.inter_name(k)
            po, pc = self.overdue_name(k), self.count_name(k)

            if self.single_blocks:
                # Maintenance
                a.append((pt, tm, "air", (lo, hi)))
                a.append(("P_bay", tm, "air", (0, INF)))
                a.append(("P_crew", tm, "dot_all", (0, INF)))
                a.append((tm, pi, "air", None))
                a.append((tm, pc, "air", None))
                # Return (individual timer reset: 1'air)
                a.append((pi, tr, "air", (0, INF)))
                a.append((tr, pt, "air", None))
                a.append((tr, "P_crew", "dot_all", None))
                a.append((tr, "P_bay", "air", None))
            # Overdue: guard lower bound = that aircraft's own deadline
            for tname, ac in self.overdue_t_names(k):
                if ac is None:
                    a.append((pt, tname, "air", (t["_uniform_inv"], INF)))
                    a.append((tname, po, "air", None))
                else:
                    a.append((pt, tname, f"const:{ac}",
                              (t["timer_invariants"][ac], INF)))
                    a.append((tname, po, f"const:{ac}", None))
            # Overdue after a reset (post-reset deadline)
            for tname, ac in self.cycle_overdue_t_names(k):
                pcy = self.cycle_name(k)
                if ac is None:
                    a.append((pcy, tname, "air", (t["_cycle_uniform_inv"], INF)))
                    a.append((tname, po, "air", None))
                else:
                    a.append((pcy, tname, f"const:{ac}",
                              (t["interval_invariants"][ac], INF)))
                    a.append((tname, po, f"const:{ac}", None))

        # Composite projects: one maintenance operation per project occurrence
        for occ in self.occurrences:
            c = f"const:{occ['aircraft']}"
            tm, tr, pi = occ["maint"], occ["return"], occ["inter"]
            for k in occ["tasks"]:
                t = self.tasks[k]
                if occ["sources"][k] == "cycle":
                    a.append((self.cycle_name(k), tm, c, t["_cycle_guard"]))
                else:
                    a.append((self.timer_name(k), tm, c, tuple(t["guard"])))
            a.append(("P_bay", tm, c, (0, INF)))
            a.append(("P_crew", tm, "dot_all", (0, INF)))
            a.append((tm, pi, "dot", None))
            for k in occ["tasks"]:
                a.append((tm, self.count_name(k), c, None))
            d = occ["duration"]
            a.append((pi, tr, "dot", (d, d)))
            for k in occ["tasks"]:
                dest = (self.cycle_name(k) if self.tasks[k]["_has_cycle"]
                        else self.timer_name(k))
                a.append((tr, dest, c, None))
            a.append((tr, "P_crew", "dot_all", None))
            a.append((tr, "P_bay", c, None))
        return a

    # ─── Net places ──────────────────────────────────────────

    def _place(self, name, sort, initial, plain_inv, color_inv=None,
               marking=None):
        """
        Generic net place.
          marking: None | 'all' | 'individual' | ('dot', n)
          color_inv: dict color -> bound (for 'aircraft') or int (for 'dot')
        """
        x, y = self.pos[name]
        lines = [f'    <place displayName="true" id="{name}" '
                 f'initialMarking="{initial}" invariant="{inv_str(plain_inv)}" '
                 f'name="{name}" nameOffsetX="0" nameOffsetY="0" '
                 f'positionX="{x}" positionY="{y}">']
        lines.append(indent(xml_type_block(sort), 3))
        if marking == "all":
            lines.append(indent(xml_hlinitialmarking_aircraft_all(1), 3))
        elif marking == "individual":
            lines.append(indent(xml_hlinitialmarking_individual(self.aircraft), 3))
        elif isinstance(marking, tuple) and marking[0] == "dot":
            lines.append(indent(xml_hlinitialmarking_dot(marking[1]), 3))
        if color_inv is not None:
            if sort == "dot":
                lines.append(indent(xml_colorinvariant(color_inv, "dot", "dot"), 3))
            else:
                for a in self.aircraft:
                    lines.append(indent(
                        xml_colorinvariant(color_inv[a], "aircraft", a), 3))
        lines.append('    </place>')
        return "\n".join(lines)

    def _all_places(self):
        return "\n".join(
            self._place(s["name"], s["sort"], s["initial"], s["plain_inv"],
                        s["color_inv"], s["marking"])
            for s in self.place_specs())

    # ─── Transitions ─────────────────────────────────────────

    def _transition(self, name):
        x, y = self.pos[name]
        return (f'    <transition angle="0" displayName="true" distribution="constant" '
                f'firingMode="Random" id="{name}" infiniteServer="false" name="{name}" '
                f'nameOffsetX="0" nameOffsetY="0" player="0" '
                f'positionX="{x}" positionY="{y}" priority="0" urgent="false" '
                f'value="1.0" weight="1.0"/>')

    def _all_transitions(self):
        return "\n".join(self._transition(n) for n in self.transition_names())

    # ─── Arcs ────────────────────────────────────────────────

    def _arc(self, source, target, kind, guard=None):
        """guard=None -> normal output arc; guard=(lo, hi) -> timed input arc."""
        aid = self._next_arc_id()
        if guard is None:
            typ, insc = "normal", "1"
        else:
            lo, hi = guard
            typ = "timed"
            insc = f"[{lo},inf)" if hi == INF else f"[{lo},{hi}]"
        sx, sy = self._center(source)
        tx, ty = self._center(target)
        return "\n".join([
            f'    <arc id="{aid}" inscription="{insc}" nameOffsetX="0" nameOffsetY="0" '
            f'source="{source}" target="{target}" type="{typ}" weight="1">',
            indent(xml_hlinscription(kind), 3),
            f'      <arcpath arcPointType="false" id="0" xCoord="{sx}" yCoord="{sy}"/>',
            f'      <arcpath arcPointType="false" id="1" xCoord="{tx}" yCoord="{ty}"/>',
            '    </arc>',
        ])

    def _all_arcs(self):
        return "\n".join(self._arc(s, t, kind, guard)
                         for s, t, kind, guard in self.arc_specs())

    # ─── Query ───────────────────────────────────────────────

    def _query(self):
        conj = "".join(
            f'            <integer-eq>\n'
            f'              <tokens-count>\n'
            f'                <place>TAPN1.{self.overdue_name(k)}</place>\n'
            f'              </tokens-count>\n'
            f'              <integer-constant>0</integer-constant>\n'
            f'            </integer-eq>\n'
            for k in range(self.n_tasks))
        return (
            f'  <query active="true" algorithmOption="CERTAIN_ZERO" '
            f'approximationDenominator="2" capacity="{self.n_ac + 2}" '
            f'colorFixpoint="false" coloredReduction="false" '
            f'discreteInclusion="false" enableOverApproximation="false" '
            f'enableUnderApproximation="false" extrapolationOption="AUTOMATIC" '
            f'gcd="false" hashTableSize="MB_16" inclusionPlaces="*NONE*" '
            f'name="No overdue violations" overApproximation="false" '
            f'pTrie="true" partitioning="false" reduction="true" '
            f'reductionOption="VerifyDTAPN" searchOption="HEURISTIC" '
            f'symmetricVars="false" symmetry="true" timeDarts="false" '
            f'traceOption="SOME" type="Default" useExplicitSearch="false" '
            f'useQueryReduction="true" useSiphonTrapAnalysis="false" '
            f'useStubbornReduction="false" useTarOption="false" useTarjan="false">\n'
            f'    <formula>\n'
            f'      <exists-path>\n'
            f'        <globally>\n'
            f'          <conjunction>\n'
            f'{conj}'
            f'          </conjunction>\n'
            f'        </globally>\n'
            f'      </exists-path>\n'
            f'    </formula>\n'
            f'  </query>'
        )

    # ─── Assembly ────────────────────────────────────────────

    def generate(self):
        self.arc_counter = 0
        return "\n".join([
            '<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
            '<pnml xmlns="http://www.informatik.hu-berlin.de/top/pnml/ptNetb">',
            self._declaration(),
            self._all_shared_places(),
            f'  <net active="true" id="{self.NET_ID}" type="P/T net">',
            self._all_places(),
            self._all_transitions(),
            self._all_arcs(),
            '  </net>',
            self._query(),
            '  <feature isColored="true" isGame="false" '
            'isStochastic="false" isTimed="true"/>',
            '</pnml>',
        ])

    def summary(self):
        lines = [
            f"  Aircraft: {self.n_ac}  ({', '.join(self.aircraft)})",
            f"  Tasks: {self.n_tasks}",
            f"  Crew: {self.crew}, Hangars: {self.hangar}, Lifespan: {self.lifespan}",
            f"  Entry guard: [{self.entry_guard_lo}, {self.entry_guard_hi}]",
            f"  Bay invariant: <= {self.bay_inv}",
        ]
        for k, t in enumerate(self.tasks):
            inv = ", ".join(f"{a}<={t['timer_invariants'][a]}" for a in self.aircraft)
            mode = "uniform" if t["_uniform"] else "per-aircraft overdue transitions"
            extra = ""
            if t["_has_cycle"]:
                extra = ", after reset: " + ", ".join(
                    f"{a}<={t['interval_invariants'][a]}" for a in self.aircraft)
            label = f" ({t['id']})" if "id" in t else ""
            if self.single_blocks:
                lines.append(f"  Task {k+1}{label}: guard={t['guard']}, timer_inv=[{inv}] "
                             f"({mode}){extra}, duration<={t['_inter_inv']}, "
                             f"overdue_inv<={t['_overdue_inv']}")
            else:
                lines.append(f"  Task {k+1}{label}: guard={t['guard']}, timer_inv=[{inv}] "
                             f"({mode}){extra}")
        if self.occurrences:
            lines.append(f"  Composite projects: {len(self.occurrences)} "
                         f"(single-task blocks {'on' if self.single_blocks else 'off'})")
            for occ in self.occurrences:
                tl = ", ".join(self.task_label(k) for k in occ["tasks"])
                start = "" if occ["start"] is None else f", start={occ['start']}"
                lines.append(f"    {occ['maint']}: tasks [{tl}], "
                             f"duration={occ['duration']}{start}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════

def main(argv=None):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(
        description="Generate a TACPN (.tapn) for TAPAAL from a JSON config.")
    parser.add_argument("config", nargs="?",
                        help="JSON config (default: example_config.json next to "
                             "this script)")
    parser.add_argument("-o", "--output",
                        help="output .tapn file (default: TACPN_<N>flights_<K>tasks"
                             ".tapn next to the config)")
    args = parser.parse_args(argv)

    if args.config:
        config_path = os.path.abspath(args.config)
    else:
        config_path = os.path.join(script_dir, "example_config.json")
    if not config_path.endswith(".json"):
        print(f"Usage: python {os.path.basename(__file__)} [config.json] [-o out.tapn]")
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)

    try:
        gen = TACPNGenerator(config)
    except ConfigError as e:
        print(f"Config error: {e}")
        sys.exit(1)

    if args.output:
        outfile = os.path.abspath(args.output)
    else:
        outfile = os.path.join(os.path.dirname(config_path),
                               f"TACPN_{gen.n_ac}flights_{gen.n_tasks}tasks.tapn")
    with open(outfile, "w", encoding="utf-8") as f:
        f.write(gen.generate())

    print(f"Generated: {os.path.basename(outfile)}")
    print(f"Saved to:  {outfile}")
    print(gen.summary())
    return outfile


if __name__ == "__main__":
    main()
