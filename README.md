# TACPN Generator for Aircraft Maintenance Scheduling

A Python tool that automatically generates **Timed-Arc Colored Petri Net (TACPN)** models in [TAPAAL](https://www.tapaal.net/)’s `.tapn` XML format for aircraft base maintenance scheduling verification.

## Motivation

Modelling aircraft maintenance scheduling as a TACPN allows formal verification of timing constraints, resource allocation, and deadline compliance using TAPAAL’s verification engine. However, manually building these models becomes impractical as the number of aircraft and maintenance tasks grows. This generator automates the process: specify your parameters in a JSON config file, run the script, and open the result directly in TAPAAL.

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

|Element               |Role                              |Type                                    |
|----------------------|----------------------------------|----------------------------------------|
|**Timer place**       |Tracks time since last maintenance|aircraft (per-aircraft color invariants)|
|**Maint transition**  |Performs the task                 |Consumes timer + bay + crew             |
|**Intermediate place**|Brief holding after task          |aircraft (auto-derived invariant)       |
|**Return transition** |Resets timer, releases resources  |Returns to bay                          |
|**Overdue transition**|Fires when deadline is exceeded   |Guard = max timer invariant             |
|**Overdue place**     |Collects violations               |Checked by verification query           |
|**Count place**       |Accumulates task completions      |aircraft                                |

### Global infrastructure

|Element              |Role                                                     |
|---------------------|---------------------------------------------------------|
|**P_flying**         |Aircraft in flight (per-aircraft color invariants)       |
|**P_bay**            |Aircraft on ground in maintenance bay                    |
|**T_enter / T_exit** |Enter/leave maintenance (consume/release hangar capacity)|
|**P_crew**           |Crew resource tokens (dot type)                          |
|**P_ground_capacity**|Hangar/parking slots (dot type)                          |
|**P_lifespan**       |Planning horizon (single dot token with age invariant)   |

## Installation

No dependencies required — the script uses only Python standard library modules (`json`, `sys`, `math`).

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
# From JSON config
python3 tacpn_generator_aegean.py example_config.json

# Or using built-in default (3 aircraft, 3 tasks)
python3 tacpn_generator_aegean.py
```

**VS Code users**: open `tacpn_generator_aegean.py` and click the ▶ Play button. See [VS Code setup](#vs-code-setup) below.

### 3. Open in TAPAAL

The generated `.tapn` file (e.g. `TACPN_3flights_2tasks.tapn`) can be opened directly in TAPAAL via **File → Open**.

## Configuration Reference

|Parameter         |Key                |Description                                    |Example             |
|------------------|-------------------|-----------------------------------------------|--------------------|
|Aircraft names    |`aircraft`         |List of aircraft identifiers                   |`["A1", "A2", "A3"]`|
|Flying time limits|`flying_invariants`|Per-aircraft max flying time before maintenance|`{"A1": 5, "A2": 6}`|
|Tasks             |`tasks`            |List of maintenance task specifications        |See below           |
|Crew count        |`crew_count`       |Number of available crew tokens                |`2`                 |
|Hangar count      |`hangar_count`     |Number of hangar/parking slots                 |`2`                 |
|Lifespan          |`lifespan`         |Planning horizon (age invariant)               |`365`               |

### Task specification

Each task in the `tasks` list contains:

|Field             |Description                                      |Example              |
|------------------|-------------------------------------------------|---------------------|
|`guard`           |`[lo, hi]` time window for performing maintenance|`[3, 8]`             |
|`timer_invariants`|Per-aircraft max age on the timer place          |`{"A1": 8, "A2": 10}`|

### Optional parameters

The defaults reproduce the hand-built reference model exactly; override them with real data where available.

|Key                         |Meaning                                            |Default                                           |
|----------------------------|---------------------------------------------------|--------------------------------------------------|
|`entry_guard`               |`[lo, hi]` guard on P_flying → T_enter             |`[max(1, ⌊0.4 × min_flying_inv⌋), max_flying_inv]`|
|`bay_invariant`             |Max time an aircraft may wait in the bay           |`3`                                               |
|`tasks[k].duration`         |Invariant of the intermediate place (task duration)|`max(1, ⌊(guard_hi − guard_lo) / 4⌋)`             |
|`tasks[k].overdue_invariant`|Invariant of the overdue place                     |`max(50, ⌊1.75 × max_timer_inv⌋)`                 |

### Uniform vs. per-aircraft deadlines

- If all aircraft share the same timer invariant for a task, the timer place gets a plain invariant and a single overdue transition `T_overdue_k` with guard `[inv, ∞)` — exactly as in the reference model.
- If the invariants differ, the timer place gets per-aircraft color invariants and the overdue transition is split into `T_overdue_k_<aircraft>` (arc inscription `1'<aircraft>`, guard `[inv_aircraft, ∞)`). This guarantees that every aircraft can reach its own overdue guard, so deadline violations are recorded instead of being hidden by a timelock.

### Validation rules

The generator checks the configuration before writing any file and stops with a clear message if, for example:

- an aircraft is missing from `flying_invariants` or from any task’s `timer_invariants`;
- a guard has `lo > hi`;
- a task’s `guard_lo` exceeds some aircraft’s timer invariant (that aircraft could never be maintained);
- `entry_guard` lower bound exceeds some aircraft’s flying invariant;
- a count is not a positive integer, or an aircraft name is not a valid identifier.

## Regression check

`check_against_reference.py` generates the 3-aircraft / 3-task model and compares every place invariant, color invariant, initial marking, shared place and arc with the reference model:

```bash
python3 check_against_reference.py TACPN_3flights_3tasks.tapn
```

The only accepted difference is the timer reset of task 3, which uses `1'air` (individual reset) for consistency with the other tasks instead of `1'aircraft.all`.

## VS Code Setup

To run directly with the ▶ button (no terminal needed):

1. Place `tacpn_generator_aegean.py` and `example_config.json` in the same folder
1. In `tacpn_generator_aegean.py`, find the `main()` function and edit the `else:` block:

```python
    else:
        with open("example_config.json") as f:
            config = json.load(f)
```

1. The `os.chdir` lines at the top of `main()` ensure the output appears in the same folder:

```python
def main():
    import os
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
```

1. Click ▶ — the `.tapn` file appears alongside the script

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

## Scaling

With N aircraft, K tasks, of which H have heterogeneous (per-aircraft) deadlines:

|Quantity   |Count              |
|-----------|-------------------|
|Places     |5 + 4K             |
|Transitions|2 + 3K + H(N − 1)  |
|Arcs       |6 + 11K + 2H(N − 1)|

Examples: 3 aircraft / 3 uniform tasks → 17 places, 11 transitions, 39 arcs (reference model). 5 aircraft / 4 heterogeneous tasks → 21 places, 30 transitions, 82 arcs.

## TAPAAL Features Used

- **Color sorts**: `aircraft` (cyclic enumeration) and `dot` (uncolored resources)
- **Per-aircraft color invariants**: different age limits per aircraft on flying and timer places
- **Timed arcs**: guards `[lo, hi]` on input arcs enforce maintenance time windows
- **Shared places**: timer places, flying place, and resource places are shared (required by TAPAAL for colored nets)
- **Verification query**: `EG[] (∀k: overdue_k = 0)` — checks whether a schedule exists that avoids all deadline violations

## License

See the `LICENSE` file in this repository.

## References

- [TAPAAL](https://www.tapaal.net/) — Tool for verification of Timed-Arc Petri Nets
- Cassandras, C.G. & Lafortune, S. (2021). *Introduction to Discrete Event Systems*. 3rd ed. Springer.
