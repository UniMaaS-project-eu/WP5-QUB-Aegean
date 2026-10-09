"""
NTUA pipeline adapter: fleet data + FLP schedule -> TACPN config (v3)
=====================================================================

Maps the outputs of NTUA's aircraft-maintenance-planning-framework to the
generator config and, optionally, runs the whole TACPN step in one call:

  python ntua_adapter.py -d examples/test.json -s test_flp_schedule.json -o test
      -> test_tacpn_config.json
  add --tapn    -> also test.tapn                      (open in TAPAAL)
  add --verify  -> also test.trc, test_tacpn_report.json (exit code 0 / 1)

Mapping:
  aircraft             schedule PIDs, characters outside [A-Za-z0-9_] replaced
                       by '_'; original ids kept in "aircraft_ids" for reports
  tasks                every task scheduled for some aircraft, plus tasks that
                       fall due before the horizon (unless --scheduled-only)
  timer_invariants     remaining life  int(max_util - curr_util) + T_dc
  interval_invariants  full interval   int(max_util), deadline after a reset
  guard                [T_dc, lifespan]             ("[0, task due date]")
  entry_guard          [0, lifespan]; flying_invariants = lifespan
  crew_count           = hangar_count = hangar_capacity (crew never binding)
  projects             the schedule's P / T / D per aircraft, in start order
  lifespan             sim_days (extended to the last project end) + T_dc
  bay_invariant        0: an aircraft holds a hangar slot only during a project
  overdue_invariant    lifespan: an overdue token never blocks time
Utilisation values are read as days, as in NTUA's tacpn.py.
"""

import argparse
import json
import os
import re
import sys

from tacpn_generator_aegean import TACPNGenerator, ConfigError


def model_name(raw, used):
    """Valid, unique aircraft identifier for the net."""
    name = re.sub(r"[^A-Za-z0-9_]", "_", str(raw))
    name = re.sub(r"_+", "_", name)
    if not re.match(r"[A-Za-z]", name):
        name = "A_" + name
    if name in ("dot", "air", "aircraft"):
        name = "A_" + name
    base, i = name, 2
    while name in used:
        name = f"{base}_{i}"
        i += 1
    used.add(name)
    return name


def load_schedule(path):
    with open(path, encoding="utf-8") as f:
        sched = json.load(f)
    if isinstance(sched, dict) and "Schedule" in sched:
        sched = sched["Schedule"]
    if not isinstance(sched, list):
        raise ConfigError(f"{path}: expected {{'Schedule': [...]}}")
    for item in sched:
        if not (len(item["P"]) == len(item["T"]) == len(item["D"])):
            raise ConfigError(f"{path}: {item['PID']}: P, T and D differ in length")
    return sched


def build_config(data, schedule, tdc=0, lifespan=None, crew=None,
                 scheduled_only=False):
    """Return (config, warnings)."""
    warnings = []
    events = {}
    for a in data["fleet"]:
        events[str(a["aircraftID"])] = {str(e["taskID"]): e for e in a["events"]}

    used, names, ids = set(), [], {}
    for item in schedule:
        pid = str(item["PID"])
        if pid in ids.values():
            raise ConfigError(f"aircraft {pid} appears twice in the schedule")
        n = model_name(pid, used)
        names.append(n)
        ids[n] = pid
        if pid not in events:
            warnings.append(f"aircraft {pid} is not in the data file")

    horizon = int(lifespan if lifespan is not None else data["sim_days"])
    last_end = max((int(t) + int(d) for item in schedule
                    for t, d in zip(item["T"], item["D"])), default=0)
    if last_end > horizon:
        warnings.append(f"horizon extended from {horizon} to {last_end} "
                        f"(last project end)")
        horizon = last_end
    life = horizon + tdc

    # Task selection: scheduled tasks (in order of appearance), then tasks
    # falling due before the horizon that no project covers.
    order, seen = [], set()
    for item in schedule:
        for proj in item["P"]:
            for t in proj:
                if str(t) not in seen:
                    seen.add(str(t))
                    order.append(str(t))
    if not scheduled_only:
        for item in schedule:
            for t, e in events.get(str(item["PID"]), {}).items():
                r = e["max_util"] - e["curr_util"]
                if t not in seen and r >= 0 and int(r) + tdc < life:
                    seen.add(t)
                    order.append(t)

    tasks, overdue_at_start, zero_interval = [], set(), 0
    for t in order:
        tinv, iv = {}, {}
        for n in names:
            e = events.get(ids[n], {}).get(t)
            if e is None or e["max_util"] - e["curr_util"] < 0:
                if e is not None:
                    overdue_at_start.add((ids[n], t))
                tinv[n], iv[n] = life, life
                continue
            tinv[n] = int(e["max_util"] - e["curr_util"]) + tdc
            iv[n] = int(e["max_util"])
            if iv[n] < 1:
                zero_interval += 1
                iv[n] = 1
        tasks.append({"id": t, "guard": [tdc, life], "timer_invariants": tinv,
                      "interval_invariants": iv, "overdue_invariant": life})
    if overdue_at_start:
        warnings.append(f"{len(overdue_at_start)} task(s) already overdue at the "
                        f"start (max_util < curr_util) are not checked, as in the "
                        f"aircraft-level TCPN")
    if zero_interval:
        warnings.append(f"{zero_interval} interval(s) shorter than one day rounded up "
                        f"to 1")

    projects = {}
    for n, item in zip(names, schedule):
        plist = sorted(zip(item["T"], item["D"], item["P"]), key=lambda x: int(x[0]))
        projects[n] = [{"tasks": [str(t) for t in p], "duration": int(d),
                        "start": int(s) + tdc} for s, d, p in plist]
        for s, d, p in plist:
            for t in p:
                if str(t) not in events.get(ids[n], {}):
                    warnings.append(f"{ids[n]}: scheduled task {t} is not in the "
                                    f"data file")

    capacity = int(data["hangar_capacity"])
    config = {
        "aircraft": names,
        "aircraft_ids": ids,
        "flying_invariants": {n: life for n in names},
        "entry_guard": [0, life],
        "bay_invariant": 0,
        "tasks": tasks,
        "crew_count": int(crew) if crew is not None else capacity,
        "hangar_count": capacity,
        "lifespan": life,
        "single_task_blocks": False,
        "projects": projects,
        "adapter": {"tdc": tdc, "warnings": warnings},
    }
    return config, warnings


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="NTUA data + FLP schedule -> TACPN config (and .tapn, trace, "
                    "verdict).")
    parser.add_argument("-d", "--data", required=True,
                        help="initial data file (e.g. examples/test.json)")
    parser.add_argument("-s", "--schedule", required=True,
                        help="FLP schedule (e.g. test_flp_schedule.json)")
    parser.add_argument("-o", "--outfile", default="out",
                        help="prefix for output files (default: out)")
    parser.add_argument("--tdc", type=int, default=0, help="time offset T_dc")
    parser.add_argument("--lifespan", type=int, help="override sim_days")
    parser.add_argument("--crew", type=int, help="crew count (default: hangar capacity)")
    parser.add_argument("--scheduled-only", action="store_true",
                        help="include only tasks scheduled for some aircraft (the "
                             "task selection of NTUA's tacpn.py)")
    parser.add_argument("--tapn", action="store_true", help="also write <prefix>.tapn")
    parser.add_argument("--verify", action="store_true",
                        help="also write <prefix>.trc and <prefix>_tacpn_report.json")
    parser.add_argument("--naming", choices=["tapaal", "ntua"], default="tapaal",
                        help="unfolded-name convention for the trace")
    args = parser.parse_args(argv)

    try:
        with open(args.data, encoding="utf-8") as f:
            data = json.load(f)
        schedule = load_schedule(args.schedule)
        config, warnings = build_config(data, schedule, args.tdc, args.lifespan,
                                        args.crew, args.scheduled_only)
        gen = TACPNGenerator(config)
    except (ConfigError, KeyError, OSError, ValueError) as e:
        print(f"Error: {e!r}" if isinstance(e, KeyError) else f"Error: {e}")
        return 2

    cfg_path = args.outfile + "_tacpn_config.json"
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    print(f"Config: {os.path.abspath(cfg_path)}")
    print(f"  {len(config['aircraft'])} aircraft, {len(config['tasks'])} tasks, "
          f"{len(gen.occurrences)} projects, hangar {config['hangar_count']}, "
          f"horizon {config['lifespan']}")
    for w in warnings:
        print(f"  warning: {w}")

    if args.tapn:
        tapn_path = args.outfile + ".tapn"
        with open(tapn_path, "w", encoding="utf-8") as f:
            f.write(gen.generate())
        print(f"TAPN:   {os.path.abspath(tapn_path)}")

    if args.verify:
        import tacpn_trace
        return tacpn_trace.main(["verify", cfg_path, "-o", args.outfile + ".trc",
                                 "--report", args.outfile + "_tacpn_report.json",
                                 "--naming", args.naming])
    return 0


if __name__ == "__main__":
    sys.exit(main())
