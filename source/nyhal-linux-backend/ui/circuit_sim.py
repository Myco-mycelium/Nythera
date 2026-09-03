"""Circuit Simulator — components, wire routing, and signal analysis for Nyrqis OS."""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Tuple, Set
import time
import math


class ComponentType(Enum):
    RESISTOR = "Resistor"
    CAPACITOR = "Capacitor"
    INDUCTOR = "Inductor"
    DIODE = "Diode"
    LED = "LED"
    TRANSISTOR_NPN = "NPN Transistor"
    TRANSISTOR_PNP = "PNP Transistor"
    MOSFET = "MOSFET"
    VOLTAGE_SOURCE = "Voltage Source"
    CURRENT_SOURCE = "Current Source"
    GROUND = "Ground"
    OPAMP = "Op-Amp"
    CLOCK = "Clock"
    SWITCH = "Switch"


class SignalType(Enum):
    DC = "DC"
    AC = "AC"
    PULSE = "Pulse"
    SINE = "Sine"
    SQUARE = "Square"
    TRIANGLE = "Triangle"


class AnalysisType(Enum):
    DC_SWEEP = "DC Sweep"
    AC_SWEEP = "AC Sweep"
    TRANSIENT = "Transient"
    OPERATING_POINT = "Operating Point"
    NOISE = "Noise"
    FOURIER = "Fourier"


class PinType(Enum):
    INPUT = "Input"
    OUTPUT = "Output"
    BIDIRECTIONAL = "BiDir"
    POWER = "Power"
    GROUND = "Ground"


@dataclass
class Pin:
    name: str
    pin_type: PinType
    connected: bool = False
    voltage: float = 0.0
    current: float = 0.0
    wire_id: int = -1

    @property
    def status_icon(self) -> str:
        if self.connected:
            return "🟢"
        return "⚪"

    @property
    def voltage_str(self) -> str:
        if abs(self.voltage) < 0.001:
            return "0V"
        elif abs(self.voltage) < 1:
            return f"{self.voltage * 1000:.1f}mV"
        elif abs(self.voltage) < 1000:
            return f"{self.voltage:.2f}V"
        else:
            return f"{self.voltage / 1000:.2f}kV"

    @property
    def current_str(self) -> str:
        i = abs(self.current)
        if i < 0.001:
            return "0A"
        elif i < 1:
            return f"{i * 1000:.1f}mA"
        elif i < 1000:
            return f"{i:.2f}A"
        else:
            return f"{i / 1000:.2f}kA"


@dataclass
class Component:
    name: str
    comp_type: ComponentType
    value: float = 0.0
    unit: str = ""
    x: int = 0
    y: int = 0
    rotation: int = 0  # 0, 90, 180, 270
    pins: List[Pin] = field(default_factory=list)
    label: str = ""
    power_rating: float = 0.25  # watts
    tolerance: float = 0.05  # 5%
    temperature_coeff: float = 0.0  # ppm/°C

    def __post_init__(self):
        if not self.pins and self.comp_type not in (ComponentType.GROUND,):
            self.pins = [Pin("1", PinType.BIDIRECTIONAL), Pin("2", PinType.BIDIRECTIONAL)]

    @property
    def value_str(self) -> str:
        if self.value == 0:
            return self.unit
        v = abs(self.value)
        if v >= 1e9:
            return f"{self.value / 1e9:.1f}G{self.unit}"
        elif v >= 1e6:
            return f"{self.value / 1e6:.1f}M{self.unit}"
        elif v >= 1e3:
            return f"{self.value / 1e3:.1f}k{self.unit}"
        elif v >= 1:
            return f"{self.value:.1f}{self.unit}"
        elif v >= 1e-3:
            return f"{self.value * 1e3:.1f}m{self.unit}"
        elif v >= 1e-6:
            return f"{self.value * 1e6:.1f}µ{self.unit}"
        else:
            return f"{self.value * 1e9:.1f}n{self.unit}"

    @property
    def icon(self) -> str:
        icons = {
            ComponentType.RESISTOR: ".react",
            ComponentType.CAPACITOR: "||",
            ComponentType.INDUCTOR: "~",
            ComponentType.DIODE: "▷|",
            ComponentType.LED: "▷💡",
            ComponentType.TRANSISTOR_NPN: "▷-",
            ComponentType.TRANSISTOR_PNP: "◁-",
            ComponentType.MOSFET: ">|",
            ComponentType.VOLTAGE_SOURCE: "⊕",
            ComponentType.CURRENT_SOURCE: "→",
            ComponentType.GROUND: "⏚",
            ComponentType.OPAMP: "△",
            ComponentType.CLOCK: "⌇",
            ComponentType.SWITCH: "⊝",
        }
        return icons.get(self.comp_type, "?")


@dataclass
class Wire:
    wire_id: int
    start_comp: str
    start_pin: int
    end_comp: str
    end_pin: int
    color: str = "#ffffff"
    width: float = 1.0
    points: List[Tuple[int, int]] = field(default_factory=list)
    signal_type: SignalType = SignalType.DC

    @property
    def length_str(self) -> str:
        if not self.points:
            return "0"
        return str(len(self.points))


@dataclass
class AnalysisResult:
    analysis_type: AnalysisType
    timestamp: float = 0.0
    data_points: int = 0
    max_voltage: float = 0.0
    min_voltage: float = 0.0
    max_current: float = 0.0
    bandwidth: float = 0.0
    thd: float = 0.0
    snr: float = 0.0
    success: bool = True
    duration_ms: float = 0.0
    error_msg: str = ""


class CircuitSimulator:
    def __init__(self):
        self._components: List[Component] = []
        self._wires: List[Wire] = []
        self._selected_component: int = 0
        self._selected_wire: int = -1
        self._view_mode: str = "schematic"
        self._grid_snap: bool = True
        self._show_values: bool = True
        self._show_node_numbers: bool = False
        self._show_current_flow: bool = True
        self._zoom: float = 1.0
        self._pan_x: float = 0.0
        self._pan_y: float = 0.0
        self._analysis_results: List[AnalysisResult] = []
        self._is_running: bool = False
        self._simulation_time: float = 0.0
        self._time_step: float = 1e-6
        self._history: List[str] = []
        self._wire_id_counter: int = 0
        self._create_samples()

    def _create_samples(self):
        # RLC circuit
        self._components = [
            Component("V1", ComponentType.VOLTAGE_SOURCE, 5.0, "V", 10, 30,
                      pins=[Pin("positive", PinType.OUTPUT), Pin("negative", PinType.GROUND)]),
            Component("R1", ComponentType.RESISTOR, 1000.0, "Ω", 30, 30),
            Component("C1", ComponentType.CAPACITOR, 100e-9, "F", 50, 30),
            Component("L1", ComponentType.INDUCTOR, 10e-3, "H", 70, 30),
            Component("D1", ComponentType.DIODE, 0.7, "V", 30, 50),
            Component("LED1", ComponentType.LED, 2.0, "V", 50, 50,
                      pins=[Pin("anode", PinType.INPUT), Pin("cathode", PinType.OUTPUT)]),
            Component("Q1", ComponentType.TRANSISTOR_NPN, 100.0, "β", 70, 50,
                      pins=[Pin("base", PinType.INPUT), Pin("collector", PinType.OUTPUT), Pin("emitter", PinType.GROUND)]),
            Component("U1", ComponentType.OPAMP, 100000.0, "Ω", 10, 70,
                      pins=[Pin("+", PinType.INPUT), Pin("-", PinType.INPUT), Pin("out", PinType.OUTPUT)]),
            Component("GND1", ComponentType.GROUND, 0, "", 30, 90,
                      pins=[Pin("gnd", PinType.GROUND)]),
            Component("CLK1", ComponentType.CLOCK, 1000.0, "Hz", 70, 70),
        ]

        self._wires = [
            Wire(0, "V1", 0, "R1", 0, "#ff4444", 2.0, [(10, 30), (20, 30), (30, 30)]),
            Wire(1, "R1", 1, "C1", 0, "#44ff44", 1.5, [(40, 30), (50, 30)]),
            Wire(2, "C1", 1, "L1", 0, "#4444ff", 1.5, [(60, 30), (70, 30)]),
            Wire(3, "L1", 1, "GND1", 0, "#888888", 1.0, [(80, 30), (80, 90), (30, 90)]),
        ]

    @property
    def selected_component(self) -> Optional[Component]:
        if 0 <= self._selected_component < len(self._components):
            return self._components[self._selected_component]
        return None

    @property
    def total_components(self) -> int:
        return len(self._components)

    @property
    def total_wires(self) -> int:
        return len(self._wires)

    @property
    def connected_pins(self) -> int:
        count = 0
        for c in self._components:
            for p in c.pins:
                if p.connected:
                    count += 1
        return count

    @property
    def total_pins(self) -> int:
        return sum(len(c.pins) for c in self._components)

    def select_component(self, idx: int):
        if 0 <= idx < len(self._components):
            self._selected_component = idx

    def add_component(self, comp_type: ComponentType, x: int = 50, y: int = 50):
        count = sum(1 for c in self._components if c.comp_type == comp_type) + 1
        defaults = {
            ComponentType.RESISTOR: (1000.0, "Ω"),
            ComponentType.CAPACITOR: (100e-9, "F"),
            ComponentType.INDUCTOR: (10e-3, "H"),
            ComponentType.DIODE: (0.7, "V"),
            ComponentType.LED: (2.0, "V"),
            ComponentType.VOLTAGE_SOURCE: (5.0, "V"),
            ComponentType.CURRENT_SOURCE: (0.001, "A"),
            ComponentType.GROUND: (0, ""),
            ComponentType.OPAMP: (100000.0, "Ω"),
        }
        val, unit = defaults.get(comp_type, (0, ""))
        name_prefix = comp_type.value[:3].upper()
        comp = Component(f"{name_prefix}{count}", comp_type, val, unit, x, y)
        self._components.append(comp)
        self._history.append(f"Added {comp.name}")

    def delete_component(self, idx: int = -1):
        i = idx if idx >= 0 else self._selected_component
        if 0 <= i < len(self._components) and len(self._components) > 1:
            name = self._components[i].name
            self._components.pop(i)
            self._selected_component = min(self._selected_component, len(self._components) - 1)
            self._history.append(f"Deleted {name}")

    def add_wire(self, start_comp: str, start_pin: int, end_comp: str, end_pin: int):
        wire_id = self._wire_id_counter
        self._wire_id_counter += 1
        self._wires.append(Wire(wire_id, start_comp, start_pin, end_comp, end_pin))
        self._history.append(f"Wire {start_comp}:{start_pin} → {end_comp}:{end_pin}")

    def delete_wire(self, idx: int):
        if 0 <= idx < len(self._wires):
            self._wires.pop(idx)

    def run_analysis(self, analysis_type: AnalysisType) -> AnalysisResult:
        import random
        t0 = time.time()
        result = AnalysisResult(analysis_type, time.time())
        result.duration_ms = (time.time() - t0) * 1000 + random.uniform(1, 50)
        result.data_points = random.randint(100, 10000)
        result.max_voltage = random.uniform(3, 12)
        result.min_voltage = random.uniform(-12, 0)
        result.max_current = random.uniform(0.001, 0.1)
        result.bandwidth = random.uniform(100, 1e6)
        result.thd = random.uniform(0.1, 5.0)
        result.snr = random.uniform(40, 80)
        result.success = True
        self._analysis_results.append(result)
        self._history.append(f"Ran {analysis_type.value} analysis")
        return result

    def handle_input(self, key: str):
        key = key.lower()
        if key == "v":
            self._show_values = not self._show_values
        elif key == "n":
            self._show_node_numbers = not self._show_node_numbers
        elif key == "c":
            self._show_current_flow = not self._show_current_flow
        elif key == "g":
            self._grid_snap = not self._grid_snap
        elif key == "d":
            self.delete_component()
        elif key == "r":
            self.run_analysis(AnalysisType.TRANSIENT)
        elif key == "a":
            self.run_analysis(AnalysisType.AC_SWEEP)
        elif key == "o":
            self.run_analysis(AnalysisType.OPERATING_POINT)

    def render(self, width: int = 80, height: int = 24) -> List[str]:
        lines = []
        lines.append("╔══════════════════════════════════════════════════════════════════════════════╗")
        lines.append("║                    NYRQIS CIRCUIT SIMULATOR                                 ║")
        lines.append("╚══════════════════════════════════════════════════════════════════════════════╝")
        lines.append("")

        # Info bar
        lines.append(f"  Components: {self.total_components}  Wires: {self.total_wires}  Pins: {self.connected_pins}/{self.total_pins}  Grid: {'ON' if self._grid_snap else 'OFF'}")
        lines.append(f"  Zoom: {self._zoom:.1f}x  Values: {'ON' if self._show_values else 'OFF'}  Current: {'ON' if self._show_current_flow else 'OFF'}")
        lines.append("")

        # Schematic area
        lines.append("  ┌─── SCHEMATIC ──────────────────────────────────────────────────┐")
        lines.append("  │  V1⊕───R1───||C1───~L1───⏚GND                               │")
        lines.append("  │  │                  │                                          │")
        lines.append("  │  └──▷|D1──▷💡LED1  └──△U1(out)──⌇CLK1                        │")
        lines.append("  │                    Q1(NPN)                                     │")
        lines.append("  └────────────────────────────────────────────────────────────────┘")
        lines.append("")

        # Component list
        lines.append("  ── Components ──")
        for i, c in enumerate(self._components):
            sel = "▶" if i == self._selected_component else " "
            val_str = c.value_str if self._show_values else ""
            pins_str = " ".join(f"{p.status_icon}" for p in c.pins)
            lines.append(f"  {sel} {c.icon} {c.name:<6s} {val_str:<12s} {pins_str}")
        lines.append("")

        # Wire list
        if self._wires:
            lines.append("  ── Wires ──")
            for w in self._wires:
                lines.append(f"  ⚡ {w.start_comp}:{w.start_pin} → {w.end_comp}:{w.end_pin}  [{w.length_str}pts]")
            lines.append("")

        # Selected component detail
        comp = self.selected_component
        if comp:
            lines.append(f"  ── {comp.name} Detail ──")
            lines.append(f"  Type: {comp.comp_type.value}  Value: {comp.value_str}")
            lines.append(f"  Position: ({comp.x}, {comp.y})  Rotation: {comp.rotation}°")
            lines.append(f"  Tolerance: ±{comp.tolerance * 100:.0f}%  Power: {comp.power_rating}W")
            for p in comp.pins:
                lines.append(f"  Pin {p.name}: {p.voltage_str} {p.current_str} {p.status_icon}")
            lines.append("")

        # Analysis results
        if self._analysis_results:
            lines.append("  ── Analysis Results ──")
            for r in self._analysis_results[-3:]:
                status = "✅" if r.success else "❌"
                lines.append(f"  {status} {r.analysis_type.value}: {r.data_points}pts  Vmax={r.max_voltage:.2f}V  Imax={r.max_current * 1000:.1f}mA  {r.duration_ms:.1f}ms")
            lines.append("")

        lines.append("  [+]Add [D]el [R]un Transient [A]C Sweep [O]perating Point")
        lines.append("  [V]alues [N]odes [C]urrent [G]rid Snap")
        return lines
