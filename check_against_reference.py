"""
Regression check: generate the 3-aircraft / 3-task model and compare it with
the hand-built reference model TACPN_3flights_3tasks.tapn (place invariants,
color invariants, initial markings, shared places, and every arc).

Usage:
  python check_against_reference.py path/to/TACPN_3flights_3tasks.tapn
"""

import os
import sys
import xml.etree.ElementTree as ET

from tacpn_generator_aegean import TACPNGenerator

NS = "{http://www.informatik.hu-berlin.de/top/pnml/ptNetb}"

REFERENCE_CONFIG = {
    "aircraft": ["A1", "A2", "A3"],
    "flying_invariants": {"A1": 5, "A2": 6, "A3": 7},
    "tasks": [
        {"guard": [3, 8],   "timer_invariants": {"A1": 8,  "A2": 8,  "A3": 8}},
        {"guard": [10, 22], "timer_invariants": {"A1": 22, "A2": 22, "A3": 22}},
        {"guard": [20, 40], "timer_invariants": {"A1": 40, "A2": 40, "A3": 40}},
    ],
    "crew_count": 2,
    "hangar_count": 2,
    "lifespan": 365,
}

# Reference name -> generator name
NAME_MAP = {
    "P0": "P_flying", "P1": "P_bay", "P5": "P_crew",
    "P_ground_capacity": "P_ground_capacity", "P_lifespan": "P_lifespan",
    "P3": "P_timer_1", "P9": "P_timer_2", "P14": "P_timer_3",
    "P2": "P_inter_1", "P6": "P_inter_2", "P13": "P_inter_3",
    "P4": "P_overdue_1", "P10": "P_overdue_2", "P15": "P_overdue_3",
    "P_task1_count": "P_count_1", "P_task2_count": "P_count_2", "P16": "P_count_3",
    "T5": "T_enter", "T6": "T_exit",
    "T8": "T_maint_1", "T11": "T_maint_2", "T14": "T_maint_3",
    "T9": "T_return_1", "T10": "T_return_2", "T13": "T_return_3",
    "T7": "T_overdue_1", "T12": "T_overdue_2", "T15": "T_overdue_3",
}

# Deliberate design difference (agreed): individual timer reset for every task
KNOWN_DIFFERENCES = {
    ("T_return_3", "P_timer_3", "normal", "1", "1'aircraft.all"):
        ("T_return_3", "P_timer_3", "normal", "1", "1'air"),
}


def places(root):
    out = {}
    for net in root.iter(NS + "net"):
        for p in net.iter(NS + "place"):
            ci = sorted((c.find(f"{NS}colortype/{NS}color").get("value"),
                         c.find(NS + "inscription").get("inscription"))
                        for c in p.iter(NS + "colorinvariant"))
            out[p.get("name")] = (p.get("invariant"), p.get("initialMarking"), tuple(ci))
    return out


def shared(root):
    return {c.get("name"): c.get("invariant")
            for c in root if c.tag == NS + "shared-place"}


def arcs(root):
    out = []
    for net in root.iter(NS + "net"):
        for a in net.iter(NS + "arc"):
            out.append((a.get("source"), a.get("target"), a.get("type"),
                        a.get("inscription"),
                        a.find(f"{NS}hlinscription/{NS}text").text))
    return sorted(out)


def main():
    ref_path = sys.argv[1] if len(sys.argv) > 1 else "TACPN_3flights_3tasks.tapn"
    ref = ET.parse(ref_path).getroot()
    gen = ET.fromstring(TACPNGenerator(REFERENCE_CONFIG).generate())
    m = lambda n: NAME_MAP.get(n, n)
    errors = []

    rp = {m(k): v for k, v in places(ref).items()}
    gp = places(gen)
    for name in sorted(set(rp) | set(gp)):
        if rp.get(name) != gp.get(name):
            errors.append(f"place {name}: ref={rp.get(name)} gen={gp.get(name)}")

    rs = {m(k): v for k, v in shared(ref).items()}
    gs = shared(gen)
    if rs != gs:
        errors.append(f"shared places: ref={rs} gen={gs}")

    ra = sorted(KNOWN_DIFFERENCES.get(x, x) for x in
                ((m(s), m(t), ty, i, h) for s, t, ty, i, h in arcs(ref)))
    ga = arcs(gen)
    for x in sorted(set(ra) - set(ga)):
        errors.append(f"arc only in reference: {x}")
    for x in sorted(set(ga) - set(ra)):
        errors.append(f"arc only in generated: {x}")

    print(f"Places: ref={len(rp)} gen={len(gp)} | Arcs: ref={len(ra)} gen={len(ga)}")
    if errors:
        print("MISMATCHES:")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print("OK: generated model matches the reference "
          "(except the agreed 1'air timer reset on task 3).")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
