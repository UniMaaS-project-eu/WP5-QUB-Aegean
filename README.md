# TACPN Generator for Aircraft Maintenance Scheduling

A Python tool that automatically generates **Timed-Arc Colored Petri Net (TACPN)** models in [TAPAAL](https://www.tapaal.net/)'s `.tapn` XML format for aircraft base maintenance scheduling verification, and verifies maintenance schedules against them without the TAPAAL GUI.

## Motivation

Modelling aircraft maintenance scheduling as a TACPN allows formal verification of timing constraints, resource allocation, and deadline compliance using TAPAAL's verification engine. However, manually building these models becomes impractical as the number of aircraft and maintenance tasks grows. This generator automates the process: specify your parameters in a JSON config file, run the script, and open the result directly in TAPAAL.

## Contents

| File | Purpose |
|---|---|
| `tacpn_generator_aegean.py` | JSON config → `.tapn` model (v3) |
| `tacpn_trace.py` | Schedule → TAPAAL trace, and trace replay: verdict + first violation (no GUI) |
| `ntua_adapter.py` | NTUA pipeline output (fleet data + FLP schedule) → config, `.tapn`, trace and verdict in one call |
| `check_against_reference.py` | Regression check against the hand-built reference model |
| `test_v3.py` | Plain-assert tests (`python3 test_v3.py`) |
| `examples/ntua_test/` | NTUA's `test.json` example with its FLP schedule, and all generated outputs |

All scripts use the Python standard library only and run on Python 3.8+.

## What's new in v3

- **Composite projects** (`projects`): a project made of several tasks is one maintenance operation. Its transition takes the timers of all its tasks at once and holds the aircraft for exactly the project duration (`[d,d]` arc). All of the project's timers are then reset together. Timers of tasks outside the project keep running. This gives task-level due-date checking with correct project durations.
- **Post-reset deadlines** (`interval_invariants`): the first deadline of a task (its remaining life) and the deadline after it has been performed (its full interval) can differ. Without this, a task performed early would be required again after its *remaining* life, which raises false violations.
- **Verification without the GUI**: `tacpn_trace.py` replays the schedule on the net's semantics and reports the first violation. The trace it writes can also be imported into the TAPAAL simulator.
- **Pipeline-friendly CLI**: config paths are resolved from the current directory. The `.tapn` is written next to the config, or to `-o`.
- Overdue transitions that could only fire at or after the planning horizon are omitted.
- Configs without the new keys produce exactly the same `.tapn` as v2.

## Model Architecture

The generated TACPN captures the following maintenance scheduling semantics:

```
                    ┌─────────────────────────────────────────────────┐
                    │              Per-Task Block (×N tasks)          │
                    │                                                 │
                    │   Timer ──[guard]──► Maint ──► Intermediate     │
                    │     ▲                  │            │           │
                    │     │                  ▼            ▼           │
                    │     └──────────── Return ◄─────────┘           │
                    │                    │   │                        │
                    │   Timer ──[ovd]──► Overdue ──► Overdue_place   │
                    │                                                 │
                    └─────────────────────────────────────────────────┘

    P_flying ──[entry guard]──► T_enter ──► P_bay ◄──► (task blocks) 
       ▲                           │                        │
       └─────── T_exit ◄──────────┘          P_crew ◄──────┘
                  │                       P_ground_capacity
                  ▼
           P_ground_capacity
```

### Elements per task

| Element | Role | Type |
|---|---|---|
| **Timer place** `P_timer_k` | Tracks time since last maintenance | aircraft (per-aircraft color invariants) |
| **Maint transition** `T_maint_k` | Performs the task | Consumes timer + bay + crew |
| **Intermediate place** `P_inter_k` | Brief holding after task | aircraft (auto-derived invariant) |
| **Return transition** `T_return_k` | Resets timer, releases resources | Returns to bay |
| **Overdue transition** `T_overdue_k` / `T_overdue_k_<A>` | Fires when the deadline is reached | Guard = that aircraft's deadline |
| **Overdue place** `P_overdue_k` | Collects violations | Checked by verification query |
| **Count place** `P_count_k` | Accumulates task completions | aircraft |
| **Post-reset timer** `P_cycle_k` (v3) | Timer after the task has been performed | aircraft (`interval_invariants`) |

### Elements per project occurrence (v3)

For aircraft `A` and its `j`-th project (in order of execution):

| Element | Role |
|---|---|
| `T_maint_<A>_P<j>` | Takes `1'A` from the timer place of every task in the project (guard = task guard), plus `P_bay` and one crew token. Increments each task's `P_count_k`. |
| `P_inter_<A>_P<j>` | `dot` place; invariant `<= d` |
| `T_return_<A>_P<j>` | Input arc `[d,d]`. Puts age-0 tokens back for every task in the project, then releases the crew and returns the aircraft to `P_bay`. |

The first execution of a task takes its token from `P_timer_k`. Later executions take it from `P_cycle_k` when the task has `interval_invariants`. When `projects` are given, the single-task blocks (`T_maint_k`, `P_inter_k`, `T_return_k`) are omitted by default (`single_task_blocks`).

### Global infrastructure

| Element | Role |
|---|---|
| **P_flying** | Aircraft in flight (per-aircraft color invariants) |
| **P_bay** | Aircraft on ground in maintenance bay |
| **T_enter / T_exit** | Enter/leave maintenance (consume/release hangar capacity) |
| **P_crew** | Crew resource tokens (dot type) |
| **P_ground_capacity** | Hangar/parking slots (dot type) |
| **P_lifespan** | Planning horizon (single dot token with age invariant) |

## Installation

No dependencies required — the scripts use only Python standard library modules.

```bash
# Verify Python 3 is available
python3 --version
```

## Usage

### 1. Configure parameters

Edit `example_config.json`:

```json
{
  "aircraft": ["A1", "A2", "A3"],
  "flying_invariants": {
    "A1": 5,
    "A2": 6,
    "A3": 7
  },
  "tasks": [
    {
      "guard": [3, 8],
      "timer_invariants": {"A1": 8, "A2": 8, "A3": 8}
    },
    {
      "guard": [10, 22],
      "timer_invariants": {"A1": 22, "A2": 22, "A3": 22}
    }
  ],
  "crew_count": 2,
  "hangar_count": 2,
  "lifespan": 365
}
```

### 2. Run the generator

```bash
# From JSON config (output next to the config)
python3 tacpn_generator_aegean.py example_config.json

# Explicit output file
python3 tacpn_generator_aegean.py my_config.json -o my_model.tapn

# Or using built-in default (example_config.json next to the script)
python3 tacpn_generator_aegean.py
```

**VS Code users**: open `tacpn_generator_aegean.py` and click the ▶ Play button. With no argument, the script reads `example_config.json` from its own folder and writes the `.tapn` there.

### 3. Open in TAPAAL

The generated `.tapn` file (e.g. `TACPN_3flights_2tasks.tapn`) can be opened directly in TAPAAL via **File → Open**.

## Integration with the NTUA pipeline

`ntua_adapter.py` reads the framework's initial data file and the FLP schedule (`<prefix>_flp_schedule.json`). It writes the TACPN config and, optionally, the `.tapn`, the trace and the verification report:

```bash
python3 ntua_adapter.py -d examples/test.json -s test_flp_schedule.json -o test --tapn --verify
```

| Output | Content |
|---|---|
| `test_tacpn_config.json` | Generator config (with projects and post-reset intervals) |
| `test.tapn` | The TACPN (open in TAPAAL) |
| `test.trc` | The schedule as a TAPAAL trace (TAPAAL simulator → import trace) |
| `test_tacpn_report.json` | Verdict and first violation; exit code 0 = feasible, 1 = infeasible |

In `pipeline.py`, the `--tacpngen`, `--tacpn` and `--tracegen` steps can be replaced by this single call:

```python
def tacpn(filename, prefix):
    flp_file = prefix + "_flp_schedule.json"
    if not isfile(flp_file):
        flp(filename, prefix)
    system(f"python TACPN/TACPN_generator/ntua_adapter.py -d {filename} "
           f"-s {flp_file} -o {prefix} --tapn --verify")
```

### Mapping

| Model element | Value |
|---|---|
| aircraft | Schedule PIDs. Characters outside `[A-Za-z0-9_]` become `_`; the original ids are kept in `aircraft_ids` and used in reports. |
| tasks | Every task scheduled for some aircraft, plus tasks that fall due before the horizon without being scheduled. With `--scheduled-only`, only scheduled tasks. |
| `timer_invariants` | Remaining life `int(max_util − curr_util)` (+ `T_dc`) |
| `interval_invariants` | Full interval `int(max_util)`: the deadline after the task has been performed |
| task `guard` | `[T_dc, lifespan]`, i.e. `[0, due date]` once combined with the invariant |
| `entry_guard`, `flying_invariants` | `[0, lifespan]` and `lifespan`. Aircraft enter the hangar only for projects. |
| `crew_count`, `hangar_count` | Both equal to `hangar_capacity`, so crew is never the binding resource |
| `projects` | The schedule's `P`/`T`/`D` per aircraft, in start order |
| `lifespan` | `sim_days`, extended to the last project end if needed (+ `T_dc`) |
| `bay_invariant` | `0`: an aircraft holds a hangar slot only during a project |

Utilisation values are read as days (all example data uses `interval_dimension = "D"`). Tasks already overdue at the start (`max_util < curr_util`) are not checked, as in the aircraft-level TCPN; the adapter prints a warning.

## Verification without the TAPAAL GUI

TAPAAL's command-line engines can verify queries, but they cannot replay a given *timed* trace: `verifydtapn` has no replay option, and `verifypn --trace-replay` is the untimed engine. `tacpn_trace.py` therefore implements the net's discrete-time semantics: token ages, guards, place and color invariants, and hangar and crew tokens.

```bash
# Simulate the schedule stored in the config (projects need "start")
python3 tacpn_trace.py verify test_tacpn_config.json -o test.trc --report report.json

# Replay an existing trace (e.g. one produced elsewhere)
python3 tacpn_trace.py check test_tacpn_config.json some_trace.trc
```

- If the schedule is feasible, the trace runs up to the horizon.
- Otherwise the trace ends with the **offending step**: a delay past a due date, or a firing without the hangar, crew or aircraft token it needs. TAPAAL's trace import should reject it at that step; `check` does.
- `check` also handles a trace that stops before the horizon: it reports any due date that would expire before the horizon.

The report's `violation` gives the counterexample for the scheduler:

```json
{"type": "capacity", "time": 22, "aircraft": "aircraft_2", "step": 16,
 "project": {"index": 2, "start": 22, "duration": 5, "tasks": ["task2", "task3", "task4"]},
 "message": "hangar capacity 1 exceeded at t=22: aircraft aircraft_2 cannot enter while ['aircraft_1'] are in maintenance"}
```

| `type` | Meaning |
|---|---|
| `due_date` | A task timer reaches its deadline without maintenance (`phase`: first due date, or after a previous execution) |
| `capacity` | No hangar slot when a project starts |
| `crew` | No crew token when a project starts |
| `aircraft_unavailable` | The aircraft is already in maintenance (overlapping projects) |
| `horizon` | A project ends after the planning horizon |
| `maintenance_window`, `task_unavailable`, `duration`, `invalid_step`, … | Mostly for traces produced elsewhere |

### Unfolded names in traces

TAPAAL traces refer to the unfolded net. Two prefix conventions have been seen so far, so both are supported. `check` accepts either; `verify --naming` selects the one to write.

| `--naming` | Transition | Shared place | Net place |
|---|---|---|---|
| `tapaal` (default) | `ComposedModel_TAPN1_T_enter__0` | `ComposedModel_Shared_P_flying__1` | `ComposedModel_TAPN1_P_bay__1` |
| `ntua` | `ComposedModel__T_enter__0` | `ComposedModel__Shared__P_flying__1` | `ComposedModel__P_bay__1` |

Aircraft-colored places carry the 0-based color index. Transitions with variable `air` are unsuffixed for the first aircraft and get `__0`, `__1`, … for the next ones. Dot places and per-aircraft (constant) transitions have no suffix. **To be confirmed in TAPAAL** by importing `examples/ntua_test/test.trc` and `test_ntua_naming.trc`.

## Configuration Reference

| Parameter | Key | Description | Example |
|---|---|---|---|
| Aircraft names | `aircraft` | List of aircraft identifiers | `["A1", "A2", "A3"]` |
| Flying time limits | `flying_invariants` | Per-aircraft max flying time before maintenance | `{"A1": 5, "A2": 6}` |
| Tasks | `tasks` | List of maintenance task specifications | See below |
| Crew count | `crew_count` | Number of available crew tokens | `2` |
| Hangar count | `hangar_count` | Number of hangar/parking slots | `2` |
| Lifespan | `lifespan` | Planning horizon (age invariant) | `365` |

### Task specification

Each task in the `tasks` list contains:

| Field | Description | Example |
|---|---|---|
| `guard` | `[lo, hi]` time window for performing maintenance | `[3, 8]` |
| `timer_invariants` | Per-aircraft max age on the timer place | `{"A1": 8, "A2": 10}` |
| `id` (optional) | Label used by projects and reports | `"task1"` |
| `interval_invariants` (optional) | Per-aircraft deadline after the task has been performed | `{"A1": 104, "A2": 103}` |

### Projects (optional, v3)

```json
"projects": {
  "A1": [
    {"tasks": ["task1", "task2"], "duration": 1, "start": 4},
    {"tasks": ["task3", "task4"], "duration": 5, "start": 20}
  ]
}
```

- `tasks` holds task ids, or 1-based task indices.
- `duration` is the exact maintenance time.
- `start` is optional for the generator but needed by `tacpn_trace.py verify`. Projects are listed in execution order.
- `"single_task_blocks"`: `true` keeps the single-task blocks next to the projects. It defaults to `false` when projects are given, and is not allowed together with `interval_invariants`.

### Optional parameters

The defaults reproduce the hand-built reference model exactly; override them with real data where available.

| Key | Meaning | Default |
|---|---|---|
| `entry_guard` | `[lo, hi]` guard on P_flying → T_enter | `[max(1, ⌊0.4 × min_flying_inv⌋), max_flying_inv]` |
| `bay_invariant` | Max time an aircraft may wait in the bay | `3` |
| `tasks[k].duration` | Invariant of the intermediate place (task duration) | `max(1, ⌊(guard_hi − guard_lo) / 4⌋)` |
| `tasks[k].overdue_invariant` | Invariant of the overdue place | `max(50, ⌊1.75 × max_timer_inv⌋)` |

### Uniform vs. per-aircraft deadlines

- If all aircraft share the same timer invariant for a task, the timer place gets a plain invariant and a single overdue transition `T_overdue_k` with guard `[inv, ∞)` — exactly as in the reference model.
- If the invariants differ, the timer place gets per-aircraft color invariants and the overdue transition is split into `T_overdue_k_<aircraft>` (arc inscription `1'<aircraft>`, guard `[inv_aircraft, ∞)`). This guarantees that every aircraft can reach its own overdue guard, so deadline violations are recorded instead of being hidden by a timelock.
- The same rules apply to `P_cycle_k` (`T_overdue_cycle_k`, `T_overdue_cycle_k_<aircraft>`).
- Overdue transitions whose guard starts at or after `lifespan` are omitted, since they could never fire before the horizon.

### Validation rules

The generator checks the configuration before writing any file and stops with a clear message if, for example:

- an aircraft is missing from `flying_invariants` or from any task's `timer_invariants` / `interval_invariants`;
- a guard has `lo > hi`;
- a task's `guard_lo` exceeds some aircraft's timer invariant (that aircraft could never be maintained);
- `entry_guard` lower bound exceeds some aircraft's flying invariant;
- a count is not a positive integer, or an aircraft name is not a valid identifier (names may not contain `__`, which TAPAAL uses for unfolded names);
- a project refers to an unknown aircraft or task, has a negative duration, or projects are not listed in order of `start`;
- `interval_invariants` are used together with single-task blocks.

## Regression check and tests

`check_against_reference.py` generates the 3-aircraft / 3-task model and compares every place invariant, color invariant, initial marking, shared place and arc with the reference model:

```bash
python3 check_against_reference.py TACPN_3flights_3tasks.tapn
```

The only accepted difference is the timer reset of task 3, which uses `1'air` (individual reset) for consistency with the other tasks instead of `1'aircraft.all`.

`test_v3.py` covers the following:

- the v2 output is unchanged;
- the reference regression passes;
- element counts are correct with projects;
- validation errors are raised;
- the NTUA example gives the expected verdicts;
- trace round trips agree in both naming conventions;
- the simulator agrees with an independent closed-form check of the scheduling rules on 600 random schedules;
- the code parses as Python 3.8 and contains no smart quotes.

```bash
python3 test_v3.py
```

## Examples

### Example 1: 3 aircraft, 3 tasks (matches reference model)

```json
{
  "aircraft": ["A1", "A2", "A3"],
  "flying_invariants": {"A1": 5, "A2": 6, "A3": 7},
  "tasks": [
    {"guard": [3, 8],   "timer_invariants": {"A1": 8,  "A2": 8,  "A3": 8}},
    {"guard": [10, 22], "timer_invariants": {"A1": 22, "A2": 22, "A3": 22}},
    {"guard": [20, 40], "timer_invariants": {"A1": 40, "A2": 40, "A3": 40}}
  ],
  "crew_count": 2,
  "hangar_count": 2,
  "lifespan": 365
}
```

Generates: 17 places, 11 transitions, 39 arcs.

### Example 2: 5 aircraft, 4 tasks (heterogeneous timer invariants)

```json
{
  "aircraft": ["A1", "A2", "A3", "A4", "A5"],
  "flying_invariants": {"A1": 4, "A2": 5, "A3": 6, "A4": 5, "A5": 7},
  "tasks": [
    {"guard": [3, 7],   "timer_invariants": {"A1": 7,  "A2": 8,  "A3": 9,  "A4": 8,  "A5": 10}},
    {"guard": [8, 18],  "timer_invariants": {"A1": 18, "A2": 20, "A3": 22, "A4": 20, "A5": 24}},
    {"guard": [15, 35], "timer_invariants": {"A1": 35, "A2": 38, "A3": 40, "A4": 38, "A5": 42}},
    {"guard": [30, 60], "timer_invariants": {"A1": 60, "A2": 65, "A3": 70, "A4": 65, "A5": 75}}
  ],
  "crew_count": 3,
  "hangar_count": 3,
  "lifespan": 365
}
```

Generates: 21 places, 30 transitions, 82 arcs (per-aircraft overdue transitions).

### Example 3: NTUA `test.json` with composite projects

`examples/ntua_test/` contains the following:

- `test.json` and `test_flp_schedule.json`, the inputs from the NTUA framework's README;
- the generated `test_tacpn_config.json`, `test.tapn` (25 places, 17 transitions, 68 arcs), `test.trc`, `test_ntua_naming.trc` and `test_tacpn_report.json` (feasible);
- an infeasible variant, in which aircraft_2's second project starts on day 22 while the only hangar is occupied: `test_infeasible_*`, which reports a capacity violation at trace step 16.

## Scaling

Let N be the number of aircraft and K the number of tasks. Without projects, let H be the number of tasks with heterogeneous (per-aircraft) deadlines, all below the horizon:

| Quantity | Count |
|---|---|
| Places | 5 + 4K |
| Transitions | 2 + 3K + H(N − 1) |
| Arcs | 6 + 11K + 2H(N − 1) |

Examples: 3 aircraft / 3 uniform tasks → 17 places, 11 transitions, 39 arcs (reference model). 5 aircraft / 4 heterogeneous tasks → 21 places, 30 transitions, 82 arcs.

With projects and single-task blocks off, let:

- O be the number of overdue transitions (timer and post-reset);
- C be the number of tasks with `interval_invariants`;
- Q be the number of project occurrences, and |S_j| the number of tasks in project j.

| Quantity | Count |
|---|---|
| Places | 5 + 3K + C + Q |
| Transitions | 2 + O + 2Q |
| Arcs | 6 + 2·O + Σ_j (3·|S_j| + 6) |

## TAPAAL Features Used

- **Color sorts**: `aircraft` (cyclic enumeration) and `dot` (uncolored resources)
- **Per-aircraft color invariants**: different age limits per aircraft on flying and timer places
- **Timed arcs**: guards `[lo, hi]` on input arcs enforce maintenance time windows; `[d,d]` fixes project durations
- **Shared places**: timer places, flying place, and resource places are shared, as in the hand-built reference model
- **Verification query**: `EG[] (∀k: overdue_k = 0)` — checks whether a schedule exists that avoids all deadline violations
- No transport arcs, inhibitor arcs or urgent transitions

## License

See the `LICENSE` file in this repository.

## References

- [TAPAAL](https://www.tapaal.net/) — Tool for verification of Timed-Arc Petri Nets
- Cassandras, C.G. & Lafortune, S. (2021). *Introduction to Discrete Event Systems*. 3rd ed. Springer.
