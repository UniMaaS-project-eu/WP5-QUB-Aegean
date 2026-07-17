"""
TACPN TAPAAL XML Generator for Aircraft Maintenance Scheduling
==============================================================

Generates a Timed-Arc Colored Petri Net (TACPN) model in TAPAAL's .tapn XML format
for aircraft base maintenance scheduling verification.

Model architecture (per task k):
  - Timer place: tracks time since last task-k maintenance (per-aircraft color invariants)
  - Maintenance transition: performs the task (guarded by time window)
  - Intermediate place: brief holding after task completion
  - Return transition: resets timer, releases resources, returns aircraft to bay
  - Overdue transition: fires when timer exceeds deadline (violation)
  - Overdue place: collects violation tokens (checked in query)
  - Count place: accumulates task completions

Usage:
  python tacpn_generator_aegean.py       
  python tacpn_generator_aegean.py config.json  # generates from JSON config file
"""

import json
import sys
import math


# ═══════════════════════════════════════════════════════════════
# XML SNIPPET HELPERS
# ═══════════════════════════════════════════════════════════════

def indent(text, level):
    """Indent each line of text by `level` × 2 spaces."""
    prefix = "  " * level
    return "\n".join(prefix + line if line.strip() else "" for line in text.split("\n"))


def xml_type_block(sort_name):
    """<type> block for a place."""
    return (
        f'<type>\n'
        f'  <text>{sort_name}</text>\n'
        f'  <structure>\n'
        f'    <usersort declaration="{sort_name}"/>\n'
        f'  </structure>\n'
        f'</type>'
    )


def xml_hlinitialmarking_aircraft_all(count):
    """hlinitialMarking using aircraft.all (one token per aircraft color)."""
    return (
        f'<hlinitialMarking>\n'
        f'  <text>{count}\'aircraft.all</text>\n'
        f'  <structure>\n'
        f'    <add>\n'
        f'      <subterm>\n'
        f'        <numberof>\n'
        f'          <subterm>\n'
        f'            <numberconstant value="{count}">\n'
        f'              <positive/>\n'
        f'            </numberconstant>\n'
        f'          </subterm>\n'
        f'          <subterm>\n'
        f'            <all>\n'
        f'              <usersort declaration="aircraft"/>\n'
        f'            </all>\n'
        f'          </subterm>\n'
        f'        </numberof>\n'
        f'      </subterm>\n'
        f'    </add>\n'
        f'  </structure>\n'
        f'</hlinitialMarking>'
    )


def xml_hlinitialmarking_individual(aircraft_names):
    """hlinitialMarking listing individual aircraft: 1'A1 + 1'A2 + ..."""
    text = " + ".join(f"1'{a}" for a in aircraft_names)
    subterms = ""
    for a in aircraft_names:
        subterms += (
            f'      <subterm>\n'
            f'        <numberof>\n'
            f'          <subterm>\n'
            f'            <numberconstant value="1">\n'
            f'              <positive/>\n'
            f'            </numberconstant>\n'
            f'          </subterm>\n'
            f'          <subterm>\n'
            f'            <useroperator declaration="{a}"/>\n'
            f'          </subterm>\n'
            f'        </numberof>\n'
            f'      </subterm>\n'
        )
    return (
        f'<hlinitialMarking>\n'
        f'  <text>{text}</text>\n'
        f'  <structure>\n'
        f'    <add>\n'
        f'{subterms}'
        f'    </add>\n'
        f'  </structure>\n'
        f'</hlinitialMarking>'
    )


def xml_hlinitialmarking_dot(count):
    """hlinitialMarking for dot tokens: N'dot."""
    return (
        f'<hlinitialMarking>\n'
        f'  <text>{count}\'dot</text>\n'
        f'  <structure>\n'
        f'    <add>\n'
        f'      <subterm>\n'
        f'        <numberof>\n'
        f'          <subterm>\n'
        f'            <numberconstant value="{count}">\n'
        f'              <positive/>\n'
        f'            </numberconstant>\n'
        f'          </subterm>\n'
        f'          <subterm>\n'
        f'            <useroperator declaration="dot"/>\n'
        f'          </subterm>\n'
        f'        </numberof>\n'
        f'      </subterm>\n'
        f'    </add>\n'
        f'  </structure>\n'
        f'</hlinitialMarking>'
    )


def xml_colorinvariant(inv_value, sort_name, color_value):
    """A single <colorinvariant> element."""
    return (
        f'<colorinvariant>\n'
        f'  <inscription inscription="&lt;= {inv_value}"/>\n'
        f'  <colortype name="{sort_name}">\n'
        f'    <color value="{color_value}"/>\n'
        f'  </colortype>\n'
        f'</colorinvariant>'
    )


def xml_hlinscription_air():
    """Arc HL inscription: 1'air (single aircraft variable)."""
    return (
        f'<hlinscription>\n'
        f'  <text>1\'air</text>\n'
        f'  <structure>\n'
        f'    <numberof>\n'
        f'      <subterm>\n'
        f'        <numberconstant value="1">\n'
        f'          <positive/>\n'
        f'        </numberconstant>\n'
        f'      </subterm>\n'
        f'      <subterm>\n'
        f'        <variable refvariable="air"/>\n'
        f'      </subterm>\n'
        f'    </numberof>\n'
        f'  </structure>\n'
        f'</hlinscription>'
    )


def xml_hlinscription_dot():
    """Arc HL inscription: 1'dot (single specific dot constant)."""
    return (
        f'<hlinscription>\n'
        f'  <text>1\'dot</text>\n'
        f'  <structure>\n'
        f'    <numberof>\n'
        f'      <subterm>\n'
        f'        <numberconstant value="1">\n'
        f'          <positive/>\n'
        f'        </numberconstant>\n'
        f'      </subterm>\n'
        f'      <subterm>\n'
        f'        <useroperator declaration="dot"/>\n'
        f'      </subterm>\n'
        f'    </numberof>\n'
        f'  </structure>\n'
        f'</hlinscription>'
    )


def xml_hlinscription_dot_all():
    """Arc HL inscription: 1'dot.all."""
    return (
        f'<hlinscription>\n'
        f'  <text>1\'dot.all</text>\n'
        f'  <structure>\n'
        f'    <numberof>\n'
        f'      <subterm>\n'
        f'        <numberconstant value="1">\n'
        f'          <positive/>\n'
        f'        </numberconstant>\n'
        f'      </subterm>\n'
        f'      <subterm>\n'
        f'        <all>\n'
        f'          <usersort declaration="dot"/>\n'
        f'        </all>\n'
        f'      </subterm>\n'
        f'    </numberof>\n'
        f'  </structure>\n'
        f'</hlinscription>'
    )


# ═══════════════════════════════════════════════════════════════
# MAIN GENERATOR
# ═══════════════════════════════════════════════════════════════

class TACPNGenerator:
    """
    Generates a TAPAAL-compatible TACPN XML model for aircraft maintenance.

    Config dict format:
    {
        "aircraft": ["A1", "A2", ...],
        "flying_invariants": {"A1": 5, "A2": 6, ...},  # per-aircraft on P_flying
        "tasks": [
            {
                "guard": [lo, hi],                        # maintenance time window
                "timer_invariants": {"A1": 8, "A2": 10, ...}  # per-aircraft on timer
            },
            ...
        ],
        "crew_count": 2,
        "hangar_count": 2,
        "lifespan": 365
    }
    """

    def __init__(self, config):
        self.aircraft = config["aircraft"]
        self.n_ac = len(self.aircraft)
        self.tasks = config["tasks"]
        self.n_tasks = len(self.tasks)
        self.flying_inv = config["flying_invariants"]
        self.crew = config["crew_count"]
        self.hangar = config["hangar_count"]
        self.lifespan = config["lifespan"]

        self.arc_counter = 0
        self._compute_derived()
        self._compute_layout()

    # ─── Auto-derived defaults ───────────────────────────────

    def _compute_derived(self):
        """Auto-derive intermediate/overdue invariants and entry guard."""
        fly_values = list(self.flying_inv.values())
        min_fly = min(fly_values)
        max_fly = max(fly_values)
        # Entry guard: [lower, upper] where lower ~ 40% of min flying inv
        self.entry_guard_lo = 0
        # self.entry_guard_lo = max(1, int(math.floor(min_fly * 0.4)))
        self.entry_guard_hi = max_fly

        for i, task in enumerate(self.tasks):
            inv_vals = list(task["timer_invariants"].values())
            max_inv = max(inv_vals)
            guard_lo, guard_hi = task["guard"]

            # Intermediate place invariant: ~half the guard range, min 1
            task["_inter_inv"] = max(1, (guard_hi - guard_lo) // 2)

            # Overdue place invariant: generous bound
            task["_overdue_inv"] = max(50, int(max_inv * 1.75))

            # Overdue transition guard lower bound = max timer invariant
            task["_overdue_guard_lo"] = max_inv

    # ─── Layout computation ──────────────────────────────────

    def _compute_layout(self):
        """Compute (x, y) positions for all net elements."""
        self.pos = {}

        # Global infrastructure y-band
        gy = 400

        self.pos["P_flying"]          = (105, gy)
        self.pos["P_ground_capacity"] = (315, gy)
        self.pos["T_enter"]           = (405, gy - 105)
        self.pos["T_exit"]            = (405, gy + 105)
        self.pos["P_bay"]             = (540, gy)
        self.pos["P_crew"]            = (1335, gy)
        self.pos["P_lifespan"]        = (255, gy + 210)

        # Tasks: first task above global, rest below
        for k in range(self.n_tasks):
            if k == 0:
                ty = gy - 210      # task row y
            else:
                ty = gy + 60 + k * 360

            # Timer place (left of maintenance)
            self.pos[f"timer_{k}"]   = (615 + k * 30, ty)
            # Maintenance transition
            self.pos[f"maint_{k}"]   = (765, ty)
            # Overdue transition (right)
            self.pos[f"overdue_t_{k}"] = (990 + k * 15, ty)
            # Overdue place (far right)
            self.pos[f"overdue_p_{k}"] = (1140 + k * 30, ty)
            # Count place (left)
            self.pos[f"count_{k}"]   = (945 - k * 60, ty - 15)

            # Intermediate place (between task row and global)
            if k == 0:
                iy = ty + 120
            else:
                iy = ty - 90
            self.pos[f"inter_{k}"]   = (765, iy)

            # Return transition (closer to global)
            if k == 0:
                ry = ty + 195
            else:
                ry = ty - 160
            self.pos[f"return_{k}"]  = (765, ry)

    # ─── ID helpers ──────────────────────────────────────────

    def _next_arc_id(self):
        aid = f"A{self.arc_counter}"
        self.arc_counter += 1
        return aid

    # ─── Place names ─────────────────────────────────────────

    def timer_name(self, k):
        return f"P_timer_{k+1}"

    def inter_name(self, k):
        return f"P_inter_{k+1}"

    def overdue_name(self, k):
        return f"P_overdue_{k+1}"

    def count_name(self, k):
        return f"P_count_{k+1}"

    def maint_name(self, k):
        return f"T_maint_{k+1}"

    def return_name(self, k):
        return f"T_return_{k+1}"

    def overdue_t_name(self, k):
        return f"T_overdue_{k+1}"

    # ─── Declaration section ─────────────────────────────────

    def _declaration(self):
        ac_constants = "\n".join(
            f'            <feconstant id="{a}" name="aircraft"/>'
            for a in self.aircraft
        )
        return (
            f'  <declaration>\n'
            f'    <structure>\n'
            f'      <declarations>\n'
            f'        <namedsort id="dot" name="dot">\n'
            f'          <dot/>\n'
            f'        </namedsort>\n'
            f'        <namedsort id="aircraft" name="aircraft">\n'
            f'          <cyclicenumeration>\n'
            f'{ac_constants}\n'
            f'          </cyclicenumeration>\n'
            f'        </namedsort>\n'
            f'        <variabledecl id="air" name="air">\n'
            f'          <usersort declaration="aircraft"/>\n'
            f'        </variabledecl>\n'
            f'      </declarations>\n'
            f'    </structure>\n'
            f'  </declaration>'
        )

    # ─── Shared places ───────────────────────────────────────

    def _shared_place_aircraft_colorinv(self, name, inv_dict, use_all_marking=True):
        """Shared place with per-aircraft color invariants, aircraft type."""
        lines = []
        lines.append(
            f'  <shared-place initialMarking="{self.n_ac}" '
            f'invariant="&lt; inf" name="{name}">'
        )
        # Color invariants first (shared-place ordering)
        for ac in self.aircraft:
            lines.append(indent(xml_colorinvariant(inv_dict[ac], "aircraft", ac), 2))
        # Type
        lines.append(indent(xml_type_block("aircraft"), 2))
        # Initial marking
        if use_all_marking:
            lines.append(indent(xml_hlinitialmarking_aircraft_all(1), 2))
        else:
            lines.append(indent(xml_hlinitialmarking_individual(self.aircraft), 2))
        lines.append(f'  </shared-place>')
        return "\n".join(lines)

    def _shared_place_dot(self, name, count):
        """Shared place with dot type, no color invariants."""
        lines = []
        lines.append(
            f'  <shared-place initialMarking="{count}" '
            f'invariant="&lt; inf" name="{name}">'
        )
        lines.append(indent(xml_type_block("dot"), 2))
        lines.append(indent(xml_hlinitialmarking_dot(count), 2))
        lines.append(f'  </shared-place>')
        return "\n".join(lines)

    def _all_shared_places(self):
        parts = []

        # Timer places (per-aircraft color invariants)
        for k, task in enumerate(self.tasks):
            parts.append(self._shared_place_aircraft_colorinv(
                self.timer_name(k), task["timer_invariants"]
            ))

        # P_flying (per-aircraft color invariants)
        parts.append(self._shared_place_aircraft_colorinv(
            "P_flying", self.flying_inv, use_all_marking=False
        ))

        # P_ground_capacity (dot)
        parts.append(self._shared_place_dot("P_ground_capacity", self.hangar))

        # P_crew (dot)
        parts.append(self._shared_place_dot("P_crew", self.crew))

        return "\n".join(parts)

    # ─── Net-internal places ─────────────────────────────────

    def _net_place_aircraft_colorinv(self, name, inv_dict, pos_key,
                                      initial=None, use_all_marking=True):
        """Net place: aircraft type, per-aircraft color invariants, with initial marking."""
        n_init = self.n_ac if initial is None else initial
        x, y = self.pos[pos_key]
        lines = []
        lines.append(
            f'    <place displayName="true" id="{name}" '
            f'initialMarking="{n_init}" invariant="&lt; inf" name="{name}" '
            f'nameOffsetX="0" nameOffsetY="0" positionX="{x}" positionY="{y}">'
        )
        lines.append(indent(xml_type_block("aircraft"), 3))
        if n_init > 0:
            if use_all_marking:
                lines.append(indent(xml_hlinitialmarking_aircraft_all(1), 3))
            else:
                lines.append(indent(xml_hlinitialmarking_individual(self.aircraft), 3))
            for ac in self.aircraft:
                lines.append(indent(xml_colorinvariant(inv_dict[ac], "aircraft", ac), 3))
        else:
            # Still need colorinvariants even if empty
            for ac in self.aircraft:
                lines.append(indent(xml_colorinvariant(inv_dict[ac], "aircraft", ac), 3))
        lines.append(f'    </place>')
        return "\n".join(lines)

    def _net_place_aircraft_uniform(self, name, invariant, pos_key,
                                     initial=0, has_marking=False):
        """Net place: aircraft type, uniform invariant, optional initial marking."""
        x, y = self.pos[pos_key]
        inv_str = f"&lt;= {invariant}" if invariant != float('inf') else "&lt; inf"
        lines = []
        lines.append(
            f'    <place displayName="true" id="{name}" '
            f'initialMarking="{initial}" invariant="{inv_str}" name="{name}" '
            f'nameOffsetX="0" nameOffsetY="0" positionX="{x}" positionY="{y}">'
        )
        lines.append(indent(xml_type_block("aircraft"), 3))
        if has_marking and initial > 0:
            lines.append(indent(xml_hlinitialmarking_aircraft_all(1), 3))
        lines.append(f'    </place>')
        return "\n".join(lines)

    def _net_place_dot(self, name, pos_key, initial=0, invariant=None,
                        has_marking=False, color_inv=None):
        """Net place: dot type."""
        x, y = self.pos[pos_key]
        if invariant is not None:
            inv_str = f"&lt;= {invariant}" if invariant != float('inf') else "&lt; inf"
        else:
            inv_str = "&lt; inf"
        lines = []
        lines.append(
            f'    <place displayName="true" id="{name}" '
            f'initialMarking="{initial}" invariant="{inv_str}" name="{name}" '
            f'nameOffsetX="0" nameOffsetY="0" positionX="{x}" positionY="{y}">'
        )
        lines.append(indent(xml_type_block("dot"), 3))
        if has_marking and initial > 0:
            lines.append(indent(xml_hlinitialmarking_dot(initial), 3))
        if color_inv is not None:
            lines.append(indent(
                xml_colorinvariant(color_inv, "dot", "dot"), 3
            ))
        lines.append(f'    </place>')
        return "\n".join(lines)

    def _transition(self, name, pos_key):
        """A single transition element."""
        x, y = self.pos[pos_key]
        return (
            f'    <transition angle="0" displayName="true" distribution="constant" '
            f'firingMode="Random" id="{name}" infiniteServer="false" name="{name}" '
            f'nameOffsetX="0" nameOffsetY="0" player="0" '
            f'positionX="{x}" positionY="{y}" priority="0" urgent="false" '
            f'value="1.0" weight="1.0"/>'
        )

    # ─── Arcs ────────────────────────────────────────────────

    def _timed_arc(self, source, target, guard_lo, guard_hi, hl_type="air"):
        """Timed (input) arc with guard [lo, hi] or [lo, inf)."""
        aid = self._next_arc_id()
        if guard_hi == float('inf') or guard_hi is None:
            insc = f"[{guard_lo},inf)"
        else:
            insc = f"[{guard_lo},{guard_hi}]"

        if hl_type == "air":
            hl = xml_hlinscription_air()
        elif hl_type == "dot":
            hl = xml_hlinscription_dot()
        elif hl_type == "dot_all":
            hl = xml_hlinscription_dot_all()
        else:
            hl = xml_hlinscription_air()

        # Compute arc path from positions
        sx, sy = self._element_center(source)
        tx, ty = self._element_center(target)

        lines = []
        lines.append(
            f'    <arc id="{aid}" inscription="{insc}" '
            f'nameOffsetX="0" nameOffsetY="0" source="{source}" target="{target}" '
            f'type="timed" weight="1">'
        )
        lines.append(indent(hl, 3))
        lines.append(f'      <arcpath arcPointType="false" id="0" xCoord="{sx}" yCoord="{sy}"/>')
        lines.append(f'      <arcpath arcPointType="false" id="1" xCoord="{tx}" yCoord="{ty}"/>')
        lines.append(f'    </arc>')
        return "\n".join(lines)

    def _normal_arc(self, source, target, hl_type="air"):
        """Normal (output) arc."""
        aid = self._next_arc_id()

        if hl_type == "air":
            hl = xml_hlinscription_air()
        elif hl_type == "dot":
            hl = xml_hlinscription_dot()
        elif hl_type == "dot_all":
            hl = xml_hlinscription_dot_all()
        else:
            hl = xml_hlinscription_air()

        sx, sy = self._element_center(source)
        tx, ty = self._element_center(target)

        lines = []
        lines.append(
            f'    <arc id="{aid}" inscription="1" '
            f'nameOffsetX="0" nameOffsetY="0" source="{source}" target="{target}" '
            f'type="normal" weight="1">'
        )
        lines.append(indent(hl, 3))
        lines.append(f'      <arcpath arcPointType="false" id="0" xCoord="{sx}" yCoord="{sy}"/>')
        lines.append(f'      <arcpath arcPointType="false" id="1" xCoord="{tx}" yCoord="{ty}"/>')
        lines.append(f'    </arc>')
        return "\n".join(lines)

    def _element_center(self, name):
        """Get approximate center coordinates for an element (place or transition)."""
        # Map element names to position keys
        pos_map = {
            "P_flying": "P_flying",
            "P_bay": "P_bay",
            "P_crew": "P_crew",
            "P_ground_capacity": "P_ground_capacity",
            "P_lifespan": "P_lifespan",
            "T_enter": "T_enter",
            "T_exit": "T_exit",
        }
        for k in range(self.n_tasks):
            pos_map[self.timer_name(k)] = f"timer_{k}"
            pos_map[self.maint_name(k)] = f"maint_{k}"
            pos_map[self.inter_name(k)] = f"inter_{k}"
            pos_map[self.return_name(k)] = f"return_{k}"
            pos_map[self.overdue_t_name(k)] = f"overdue_t_{k}"
            pos_map[self.overdue_name(k)] = f"overdue_p_{k}"
            pos_map[self.count_name(k)] = f"count_{k}"

        key = pos_map.get(name)
        if key and key in self.pos:
            x, y = self.pos[key]
            # Places are 30x30, center offset ~15; transitions similar
            return (x + 15, y + 15)
        return (400, 400)  # fallback

    # ─── Query section ───────────────────────────────────────

    def _query(self):
        """EG[] query: all overdue places remain empty."""
        conjuncts = ""
        for k in range(self.n_tasks):
            pname = self.overdue_name(k)
            conjuncts += (
                f'            <integer-eq>\n'
                f'              <tokens-count>\n'
                f'                <place>TAPN1.{pname}</place>\n'
                f'              </tokens-count>\n'
                f'              <integer-constant>0</integer-constant>\n'
                f'            </integer-eq>\n'
            )
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
            f'{conjuncts}'
            f'          </conjunction>\n'
            f'        </globally>\n'
            f'      </exists-path>\n'
            f'    </formula>\n'
            f'  </query>'
        )

    # ─── Full net assembly ───────────────────────────────────

    def _all_places(self):
        """Generate all net-internal place elements."""
        parts = []

        # ── P_flying: aircraft, per-aircraft color invariants ──
        parts.append(self._net_place_aircraft_colorinv(
            "P_flying", self.flying_inv, "P_flying",
            initial=self.n_ac, use_all_marking=False
        ))

        # ── P_bay: aircraft, invariant = max of intermediate invariants + 2 ──
        max_inter = max(t["_inter_inv"] for t in self.tasks)
        bay_inv = max_inter + 2
        parts.append(self._net_place_aircraft_uniform(
            "P_bay", bay_inv, "P_bay", initial=0
        ))

        # ── P_crew: dot, inf invariant ──
        parts.append(self._net_place_dot(
            "P_crew", "P_crew", initial=self.crew,
            has_marking=True
        ))

        # ── P_ground_capacity: dot, inf invariant ──
        parts.append(self._net_place_dot(
            "P_ground_capacity", "P_ground_capacity",
            initial=self.hangar, has_marking=True
        ))

        # ── P_lifespan: dot with color invariant ──
        parts.append(self._net_place_dot(
            "P_lifespan", "P_lifespan", initial=1,
            has_marking=True, color_inv=self.lifespan
        ))

        # ── Per-task places ──
        for k, task in enumerate(self.tasks):
            # Timer place (per-aircraft color invariants)
            parts.append(self._net_place_aircraft_colorinv(
                self.timer_name(k), task["timer_invariants"], f"timer_{k}",
                initial=self.n_ac, use_all_marking=True
            ))

            # Intermediate place (uniform invariant)
            parts.append(self._net_place_aircraft_uniform(
                self.inter_name(k), task["_inter_inv"], f"inter_{k}"
            ))

            # Overdue place (uniform invariant)
            parts.append(self._net_place_aircraft_uniform(
                self.overdue_name(k), task["_overdue_inv"], f"overdue_p_{k}"
            ))

            # Count place (inf invariant)
            parts.append(self._net_place_aircraft_uniform(
                self.count_name(k), float('inf'), f"count_{k}"
            ))

        return "\n".join(parts)

    def _all_transitions(self):
        """Generate all transition elements."""
        parts = []

        # Global entry/exit
        parts.append(self._transition("T_enter", "T_enter"))
        parts.append(self._transition("T_exit", "T_exit"))

        # Per-task transitions
        for k in range(self.n_tasks):
            parts.append(self._transition(self.maint_name(k), f"maint_{k}"))
            parts.append(self._transition(self.return_name(k), f"return_{k}"))
            parts.append(self._transition(self.overdue_t_name(k), f"overdue_t_{k}"))

        return "\n".join(parts)

    def _all_arcs(self):
        """Generate all arc elements."""
        parts = []

        # ════════════════════════════════════════════════
        # GLOBAL ENTRY/EXIT ARCS
        # ════════════════════════════════════════════════

        # P_flying → T_enter [entry_guard] (aircraft enters maintenance)
        parts.append(self._timed_arc(
            "P_flying", "T_enter",
            self.entry_guard_lo, self.entry_guard_hi, "air"
        ))

        # P_ground_capacity → T_enter [0,inf) (consume hangar spot)
        parts.append(self._timed_arc(
            "P_ground_capacity", "T_enter", 0, float('inf'), "dot"
        ))

        # T_enter → P_bay (aircraft arrives at bay)
        parts.append(self._normal_arc("T_enter", "P_bay", "air"))

        # P_bay → T_exit [0,inf) (aircraft leaves bay)
        parts.append(self._timed_arc(
            "P_bay", "T_exit", 0, float('inf'), "air"
        ))

        # T_exit → P_flying (aircraft returns to flying)
        parts.append(self._normal_arc("T_exit", "P_flying", "air"))

        # T_exit → P_ground_capacity (release hangar spot)
        parts.append(self._normal_arc("T_exit", "P_ground_capacity", "dot"))

        # ════════════════════════════════════════════════
        # PER-TASK ARCS
        # ════════════════════════════════════════════════

        for k, task in enumerate(self.tasks):
            guard_lo, guard_hi = task["guard"]
            overdue_lo = task["_overdue_guard_lo"]
            tn_maint = self.maint_name(k)
            tn_return = self.return_name(k)
            tn_overdue = self.overdue_t_name(k)
            pn_timer = self.timer_name(k)
            pn_inter = self.inter_name(k)
            pn_overdue = self.overdue_name(k)
            pn_count = self.count_name(k)

            # ── Maintenance transition inputs ──

            # Timer → Maint [guard_lo, guard_hi]
            parts.append(self._timed_arc(
                pn_timer, tn_maint, guard_lo, guard_hi, "air"
            ))

            # P_bay → Maint [0,inf)
            parts.append(self._timed_arc(
                "P_bay", tn_maint, 0, float('inf'), "air"
            ))

            # P_crew → Maint [0,inf)
            parts.append(self._timed_arc(
                "P_crew", tn_maint, 0, float('inf'), "dot_all"
            ))

            # ── Maintenance transition outputs ──

            # Maint → Intermediate
            parts.append(self._normal_arc(tn_maint, pn_inter, "air"))

            # Maint → Count
            parts.append(self._normal_arc(tn_maint, pn_count, "air"))

            # ── Return transition inputs ──

            # Intermediate → Return [0,inf)
            parts.append(self._timed_arc(
                pn_inter, tn_return, 0, float('inf'), "air"
            ))

            # ── Return transition outputs ──

            # Return → Timer (reset, 1'air — individual reset)
            parts.append(self._normal_arc(tn_return, pn_timer, "air"))

            # Return → P_crew (release crew)
            parts.append(self._normal_arc(tn_return, "P_crew", "dot_all"))

            # Return → P_bay (back to bay, ready for next task or exit)
            parts.append(self._normal_arc(tn_return, "P_bay", "air"))

            # ── Overdue transition ──

            # Timer → Overdue [max_inv, inf)
            parts.append(self._timed_arc(
                pn_timer, tn_overdue, overdue_lo, float('inf'), "air"
            ))

            # Overdue → Overdue place
            parts.append(self._normal_arc(tn_overdue, pn_overdue, "air"))

        return "\n".join(parts)

    # ─── Full XML assembly ───────────────────────────────────

    def generate(self):
        """Generate the complete TAPAAL .tapn XML string."""
        parts = []
        parts.append('<?xml version="1.0" encoding="UTF-8" standalone="no"?>')
        parts.append('<pnml xmlns="http://www.informatik.hu-berlin.de/top/pnml/ptNetb">')

        # Declaration
        parts.append(self._declaration())

        # Shared places
        parts.append(self._all_shared_places())

        # Net
        parts.append('  <net active="true" id="TAPN1" type="P/T net">')
        parts.append(self._all_places())
        parts.append(self._all_transitions())
        parts.append(self._all_arcs())
        parts.append('  </net>')

        # Query
        parts.append(self._query())

        # Feature
        parts.append(
            '  <feature isColored="true" isGame="false" '
            'isStochastic="false" isTimed="true"/>'
        )
        parts.append('</pnml>')

        return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# EXAMPLE CONFIGURATION & ENTRY POINT
# ═══════════════════════════════════════════════════════════════

def example_config_3ac_3tasks():
    """
    Example: 3 aircraft, 3 tasks.
    Matches the reference TACPN_3flights_3tasks.tapn model.
    """
    return {
        "aircraft": ["A1", "A2", "A3"],
        "flying_invariants": {
            "A1": 5,
            "A2": 6,
            "A3": 7,
        },
        "tasks": [
            {
                "guard": [3, 8],
                "timer_invariants": {"A1": 8, "A2": 8, "A3": 8},
            },
            {
                "guard": [10, 22],
                "timer_invariants": {"A1": 22, "A2": 22, "A3": 22},
            },
            {
                "guard": [20, 40],
                "timer_invariants": {"A1": 40, "A2": 40, "A3": 40},
            },
        ],
        "crew_count": 2,
        "hangar_count": 2,
        "lifespan": 365,
    }


def example_config_5ac_4tasks():
    """
    Larger example: 5 aircraft with heterogeneous timer invariants, 4 tasks.
    Demonstrates per-aircraft color invariants on timer places.
    """
    return {
        "aircraft": ["A1", "A2", "A3", "A4", "A5"],
        "flying_invariants": {
            "A1": 4,
            "A2": 5,
            "A3": 6,
            "A4": 5,
            "A5": 7,
        },
        "tasks": [
            {
                "guard": [3, 7],
                "timer_invariants": {
                    "A1": 7, "A2": 8, "A3": 9, "A4": 8, "A5": 10,
                },
            },
            {
                "guard": [8, 18],
                "timer_invariants": {
                    "A1": 18, "A2": 20, "A3": 22, "A4": 20, "A5": 24,
                },
            },
            {
                "guard": [15, 35],
                "timer_invariants": {
                    "A1": 35, "A2": 38, "A3": 40, "A4": 38, "A5": 42,
                },
            },
            {
                "guard": [30, 60],
                "timer_invariants": {
                    "A1": 60, "A2": 65, "A3": 70, "A4": 65, "A5": 75,
                },
            },
        ],
        "crew_count": 3,
        "hangar_count": 3,
        "lifespan": 365,
    }


def main():
    import os
    os.chdir (os.path.dirname(os.path.abspath(__file__)))

    if len(sys.argv) > 1:
        config_path = sys.argv[1]
        if config_path.endswith(".json"):
            with open(config_path) as f:
                config = json.load(f)
        else:
            print(f"Usage: python {sys.argv[0]} [config.json]")
            sys.exit(1)
    else:
        # Customized parameter
        with open("example_config.json") as f:
            config = json.load(f)

    gen = TACPNGenerator(config)
    xml = gen.generate()

    # Determine output filename
    n_ac = len(config["aircraft"])
    n_tasks = len(config["tasks"])
    outfile = f"TACPN_{n_ac}flights_{n_tasks}tasks.tapn"

    with open(outfile, "w", encoding="utf-8") as f:
        f.write(xml)

    print(f"Generated: {outfile}")
    print(f"  Aircraft: {n_ac}  ({', '.join(config['aircraft'])})")
    print(f"  Tasks: {n_tasks}")
    print(f"  Crew: {config['crew_count']}, Hangars: {config['hangar_count']}")
    print(f"  Lifespan: {config['lifespan']}")
    print(f"  Entry guard (auto): [{gen.entry_guard_lo}, {gen.entry_guard_hi}]")
    for i, t in enumerate(config["tasks"]):
        inv_str = ", ".join(
            f"{ac}≤{t['timer_invariants'][ac]}" for ac in config["aircraft"]
        )
        print(f"  Task {i+1}: guard={t['guard']}, timer_inv=[{inv_str}], "
              f"inter_inv={t['_inter_inv']}, overdue_inv={t['_overdue_inv']}")

    return outfile


if __name__ == "__main__":
    main()
