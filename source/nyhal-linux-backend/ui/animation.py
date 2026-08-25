"""
NUI Animation Engine (NUI-SCHEMA §8.3)
=======================================

Turns declarative ``NstudioAnimation`` objects into live, frame-by-frame
property interpolation that the compositor or desktop session can sample
each frame.

Public API
----------
Easing functions
    linear, ease_in, ease_out, ease_in_out, cubic_bezier, spring

Keyframe interpolation
    interpolate_keyframes(kfs, progress, prop_type) -> value

AnimationTimeline
    Manages a set of running :class:`RunningAnimation` instances.
    Call ``tick(elapsed_ms)`` every frame; read back current property
    values via ``snapshot()``.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Easing functions
# ---------------------------------------------------------------------------

def linear(t: float) -> float:
    """No acceleration."""
    return t


def ease_in(t: float) -> float:
    """Slow start, fast end — quadratic."""
    return t * t


def ease_out(t: float) -> float:
    """Fast start, slow end — quadratic."""
    return t * (2 - t)


def ease_in_out(t: float) -> float:
    """Slow start + end — quadratic."""
    if t < 0.5:
        return 2 * t * t
    return -1 + (4 - 2 * t) * t


def ease_in_cubic(t: float) -> float:
    """Slow start, cubic."""
    return t * t * t


def ease_out_cubic(t: float) -> float:
    """Fast start, cubic."""
    t -= 1
    return t * t * t + 1


def ease_in_out_cubic(t: float) -> float:
    """Slow start + end, cubic."""
    if t < 0.5:
        return 4 * t * t * t
    t = 2 * t - 2
    return 0.5 * t * t * t + 1


def cubic_bezier(c1x: float, c1y: float, c2x: float, c2y: float) -> Callable[[float], float]:
    """Return an easing function for the given cubic-bezier control points.

    Uses Newton-Raphson to solve for *t* given *x*.
    """
    def _solve(x: float) -> float:
        # Newton-Raphson to find t such that B(t).x == x
        t = x  # initial guess
        for _ in range(8):
            bx = _bezier_x(t, c1x, c2x) - x
            if abs(bx) < 1e-6:
                break
            dx = _bezier_dx(t, c1x, c2x)
            if abs(dx) < 1e-10:
                break
            t -= bx / dx
        t = max(0.0, min(1.0, t))
        return _bezier_y(t, c1y, c2y)

    def _easing(t: float) -> float:
        if t <= 0:
            return 0.0
        if t >= 1:
            return 1.0
        return _solve(t)

    return _easing


def _bezier_x(t: float, c1x: float, c2x: float) -> float:
    return 3 * (1 - t) ** 2 * t * c1x + 3 * (1 - t) * t ** 2 * c2x + t ** 3


def _bezier_y(t: float, c1y: float, c2y: float) -> float:
    return 3 * (1 - t) ** 2 * t * c1y + 3 * (1 - t) * t ** 2 * c2y + t ** 3


def _bezier_dx(t: float, c1x: float, c2x: float) -> float:
    return (
        3 * (1 - t) ** 2 * c1x
        + 6 * (1 - t) * t * (c2x - c1x)
        + 3 * t ** 2 * (1 - c2x)
    )


def spring(
    damping: float = 0.5,
    stiffness: float = 100.0,
    mass: float = 1.0,
) -> Callable[[float], float]:
    """Return a spring-physics easing function.

    Returns a function that takes progress (0–1) and returns an
    interpolated value that may overshoot past 1 before settling.
    """

    def _spring(t: float) -> float:
        if t <= 0:
            return 0.0
        if t >= 1:
            return 1.0
        omega = math.sqrt(stiffness / mass)
        zeta = damping / (2 * math.sqrt(stiffness * mass))
        if zeta < 1:
            # Under-damped
            omega_d = omega * math.sqrt(1 - zeta ** 2)
            return 1 - math.exp(-zeta * omega * t) * (
                math.cos(omega_d * t)
                + (zeta * omega / omega_d) * math.sin(omega_d * t)
            )
        else:
            # Critically or over-damped
            return 1 - (1 + omega * t) * math.exp(-omega * t)

    return _spring


# Built-in easing lookup table
EASINGS: Dict[str, Callable[[float], float]] = {
    "linear": linear,
    "ease-in": ease_in,
    "ease-out": ease_out,
    "ease-in-out": ease_in_out,
    "ease-in-cubic": ease_in_cubic,
    "ease-out-cubic": ease_out_cubic,
    "ease-in-out-cubic": ease_in_out_cubic,
    "spring": spring(),
}


def get_easing(name: str) -> Callable[[float], float]:
    """Look up an easing function by name, falling back to linear."""
    if name in EASINGS:
        return EASINGS[name]
    # Try parsing cubic-bezier(a,b,c,d)
    if name.startswith("cubic-bezier(") and name.endswith(")"):
        parts = name[len("cubic-bezier("):-1].split(",")
        if len(parts) == 4:
            nums = [float(p.strip()) for p in parts]
            return cubic_bezier(*nums)
    return linear


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def _parse_color(value: str) -> Tuple[int, int, int, int]:
    """Parse '#rrggbb', '#rrggbbaa', 'rgb(r,g,b)', or 'rgba(r,g,b,a)'."""
    v = value.strip()
    if v.startswith("#"):
        hex_str = v[1:]
        if len(hex_str) == 6:
            r = int(hex_str[0:2], 16)
            g = int(hex_str[2:4], 16)
            b = int(hex_str[4:6], 16)
            return (r, g, b, 255)
        elif len(hex_str) == 8:
            r = int(hex_str[0:2], 16)
            g = int(hex_str[2:4], 16)
            b = int(hex_str[4:6], 16)
            a = int(hex_str[6:8], 16)
            return (r, g, b, a)
    if v.startswith("rgba(") and v.endswith(")"):
        parts = v[5:-1].split(",")
        if len(parts) == 4:
            return tuple(int(float(p.strip())) for p in parts)  # type: ignore
    if v.startswith("rgb(") and v.endswith(")"):
        parts = v[4:-1].split(",")
        if len(parts) == 3:
            return (int(float(parts[0])), int(float(parts[1])), int(float(parts[2])), 255)
    return (0, 0, 0, 255)


def _color_to_str(r: int, g: int, b: int, a: int) -> str:
    """Return '#rrggbb' or '#rrggbbaa'."""
    if a == 255:
        return f"#{r:02x}{g:02x}{b:02x}"
    return f"#{r:02x}{g:02x}{b:02x}{a:02x}"


# ---------------------------------------------------------------------------
# Interpolation
# ---------------------------------------------------------------------------

def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _interpolate_value(from_val: Any, to_val: Any, t: float) -> Any:
    """Interpolate between two values of the same type."""
    # Both numeric
    if isinstance(from_val, (int, float)) and isinstance(to_val, (int, float)):
        return _lerp(float(from_val), float(to_val), t)

    # Both strings that look like colors
    if isinstance(from_val, str) and isinstance(to_val, str):
        try:
            fc = _parse_color(from_val)
            tc = _parse_color(to_val)
            return _color_to_str(
                round(_lerp(fc[0], tc[0], t)),
                round(_lerp(fc[1], tc[1], t)),
                round(_lerp(fc[2], tc[2], t)),
                round(_lerp(fc[3], tc[3], t)),
            )
        except Exception:
            pass

    # Both booleans
    if isinstance(from_val, bool) and isinstance(to_val, bool):
        return to_val if t >= 0.5 else from_val

    # Both strings — snap at midpoint
    if isinstance(from_val, str) and isinstance(to_val, str):
        return to_val if t >= 0.5 else from_val

    # Fallback — snap at midpoint
    return to_val if t >= 0.5 else from_val


def _detect_property_type(value: Any) -> str:
    """Detect the interpolation type of a value."""
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        try:
            _parse_color(value)
            return "color"
        except Exception:
            pass
        return "string"
    if isinstance(value, bool):
        return "boolean"
    return "number"


def interpolate_keyframes(
    keyframes: List[Dict[str, Any]],
    progress: float,
    from_value: Any = None,
    to_value: Any = None,
) -> Any:
    """Interpolate through an ordered list of keyframes.

    Each keyframe: ``{"offset": 0.0–1.0, "value": ...}``
    ``progress`` is 0.0–1.0 (clamped).
    """
    if not keyframes:
        # Simple two-point interpolation
        if from_value is not None and to_value is not None:
            return _interpolate_value(from_value, to_value, progress)
        return progress

    progress = max(0.0, min(1.0, progress))

    # Find the two surrounding keyframes
    sorted_kfs = sorted(keyframes, key=lambda k: k.get("offset", 0.0))

    if progress <= sorted_kfs[0].get("offset", 0.0):
        return sorted_kfs[0]["value"]
    if progress >= sorted_kfs[-1].get("offset", 1.0):
        return sorted_kfs[-1]["value"]

    for i in range(len(sorted_kfs) - 1):
        k0 = sorted_kfs[i]
        k1 = sorted_kfs[i + 1]
        off0 = k0.get("offset", 0.0)
        off1 = k1.get("offset", 1.0)
        if off0 <= progress <= off1:
            segment_len = off1 - off0
            if segment_len < 1e-10:
                return k0["value"]
            local_t = (progress - off0) / segment_len
            return _interpolate_value(k0["value"], k1["value"], local_t)

    return sorted_kfs[-1]["value"]


# ---------------------------------------------------------------------------
# Running animation
# ---------------------------------------------------------------------------

@dataclass
class RunningAnimation:
    """One in-flight animation instance."""
    animation_id: str
    target_id: str
    property_name: str
    easing_fn: Callable[[float], float]
    duration_ms: int
    delay_ms: int
    repeat_count: int  # 0 = play once, -1 = infinite, N = play N+1 times
    direction: str  # "forward", "reverse", "alternate"
    keyframes: List[Dict[str, Any]]
    from_value: Any = None
    to_value: Any = None

    # Runtime state
    start_time_ms: float = 0.0
    elapsed_ms: float = 0.0
    completed: bool = False
    _current_iteration: int = 0
    _reversed: bool = False

    @property
    def target_property(self) -> str:
        return f"{self.target_id}.{self.property_name}"


# ---------------------------------------------------------------------------
# Animation timeline
# ---------------------------------------------------------------------------

class AnimationTimeline:
    """Manages all running animations.

    Typical usage::

        timeline = AnimationTimeline()

        # Start an animation
        timeline.play(
            animation_id="startMenuOpen",
            target_id="startMenu",
            property_name="opacity",
            from_value=0.0,
            to_value=1.0,
            duration_ms=250,
            easing="ease-out",
        )

        # Each frame
        timeline.tick(elapsed_ms)
        values = timeline.snapshot()  # {"startMenu.opacity": 0.87}
    """

    def __init__(self) -> None:
        self._running: List[RunningAnimation] = []
        self._completed_pending: List[RunningAnimation] = []
        self._on_complete: Optional[Callable[[str], None]] = None

    def set_on_complete(self, callback: Callable[[str], None]) -> None:
        """Set a callback invoked when an animation completes (animation_id)."""
        self._on_complete = callback

    @property
    def active_count(self) -> int:
        return len(self._running)

    def play(
        self,
        animation_id: str,
        target_id: str,
        property_name: str,
        from_value: Any = None,
        to_value: Any = None,
        duration_ms: int = 300,
        delay_ms: int = 0,
        easing: str = "ease-in-out",
        repeat: int = 0,
        direction: str = "forward",
        keyframes: Optional[List[Dict[str, Any]]] = None,
        now_ms: Optional[float] = None,
    ) -> RunningAnimation:
        """Start a new animation.  Returns the :class:`RunningAnimation`."""
        ra = RunningAnimation(
            animation_id=animation_id,
            target_id=target_id,
            property_name=property_name,
            easing_fn=get_easing(easing),
            duration_ms=max(1, duration_ms),
            delay_ms=max(0, delay_ms),
            repeat_count=repeat,
            direction=direction,
            keyframes=keyframes or [],
            from_value=from_value,
            to_value=to_value,
            start_time_ms=now_ms or (time.time() * 1000),
        )
        self._running.append(ra)
        return ra

    def play_from_nstudio(self, anim: Any, state: Optional[Dict[str, Any]] = None) -> RunningAnimation:
        """Start an animation from an :class:`NstudioAnimation` dataclass.

        *state* is the current state dict; ``anim.from_value`` and
        ``anim.to_value`` may be ``$state:`` references resolved here.
        """
        from_val = getattr(anim, "from_value", None)
        to_val = getattr(anim, "to_value", None)

        # Resolve $state: references
        if isinstance(from_val, str) and from_val.startswith("$state:"):
            key = from_val[7:]
            from_val = (state or {}).get(key)
        if isinstance(to_val, str) and to_val.startswith("$state:"):
            key = to_val[7:]
            to_val = (state or {}).get(key)

        return self.play(
            animation_id=anim.id,
            target_id=anim.target,
            property_name=anim.property,
            from_value=from_val,
            to_value=to_val,
            duration_ms=getattr(anim, "duration", 300),
            delay_ms=getattr(anim, "delay", 0),
            easing=getattr(anim, "easing", "ease-in-out"),
            repeat=getattr(anim, "repeat", 0),
            direction=getattr(anim, "direction", "forward"),
            keyframes=getattr(anim, "keyframes", None),
        )

    def stop(self, animation_id: str) -> bool:
        """Stop an animation by id.  Returns True if found."""
        before = len(self._running)
        self._running = [a for a in self._running if a.animation_id != animation_id]
        return len(self._running) < before

    def stop_target(self, target_id: str) -> int:
        """Stop all animations targeting a component.  Returns count stopped."""
        before = len(self._running)
        self._running = [a for a in self._running if a.target_id != target_id]
        return before - len(self._running)

    def stop_all(self) -> int:
        """Stop all animations.  Returns count stopped."""
        count = len(self._running)
        self._running.clear()
        return count

    def tick(self, elapsed_ms: float, now_ms: Optional[float] = None) -> None:
        """Advance all running animations by *elapsed_ms*.

        Call once per frame (typically ~16 ms for 60 fps).
        """
        now = now_ms or (time.time() * 1000)
        completed: List[RunningAnimation] = []

        still_running: List[RunningAnimation] = []
        for ra in self._running:
            ra.elapsed_ms += elapsed_ms

            # Check if delay hasn't elapsed yet
            if ra.elapsed_ms < ra.delay_ms:
                still_running.append(ra)
                continue

            active_elapsed = ra.elapsed_ms - ra.delay_ms
            total_duration = ra.duration_ms
            if total_duration <= 0:
                total_duration = 1

            iteration_progress = active_elapsed / total_duration

            # Check if this iteration is done
            if iteration_progress >= 1.0:
                # How many full iterations?
                full_iters = int(active_elapsed / total_duration)
                remaining = active_elapsed - full_iters * total_duration

                ra._current_iteration += full_iters

                if ra.repeat_count == 0:
                    # Single play — done
                    ra.completed = True
                    completed.append(ra)
                    continue
                elif ra.repeat_count > 0 and ra._current_iteration > ra.repeat_count:
                    ra.completed = True
                    completed.append(ra)
                    continue
                elif ra.repeat_count == -1:
                    # Infinite — keep going
                    ra.elapsed_ms = ra.delay_ms + remaining
                    iteration_progress = remaining / total_duration
                    if ra.direction == "alternate":
                        ra._reversed = not ra._reversed
                else:
                    # Finite repeats
                    ra.elapsed_ms = ra.delay_ms + remaining
                    iteration_progress = remaining / total_duration
                    if ra.direction == "alternate":
                        ra._reversed = not ra._reversed

            # Apply easing
            if ra._reversed:
                iteration_progress = 1.0 - iteration_progress
            eased = ra.easing_fn(iteration_progress)

            still_running.append(ra)

        self._running = still_running
        self._completed_pending.extend(completed)

        # Fire completion callbacks
        if self._on_complete:
            for ra in completed:
                self._on_complete(ra.animation_id)

    def snapshot(self) -> Dict[str, Any]:
        """Return a dict of ``{target.property: current_value}`` for all
        running animations.  Completed animations that haven't been
        consumed are included with their final value."""
        result: Dict[str, Any] = {}
        # Include completed animations that haven't been snapshot yet
        for ra in getattr(self, '_completed_pending', []):
            result[ra.target_property] = ra.to_value
        for ra in self._running:
            if ra.elapsed_ms < ra.delay_ms:
                # Still in delay — use from_value
                result[ra.target_property] = ra.from_value
                continue

            active_elapsed = ra.elapsed_ms - ra.delay_ms
            total_duration = max(1, ra.duration_ms)
            progress = min(1.0, active_elapsed / total_duration)

            if ra._reversed:
                progress = 1.0 - progress
            eased = ra.easing_fn(progress)

            value = interpolate_keyframes(
                ra.keyframes, eased, ra.from_value, ra.to_value
            )
            result[ra.target_property] = value

        return result

    def get_property(self, target_id: str, property_name: str) -> Any:
        """Get the current animated value of a specific property, or None."""
        key = f"{target_id}.{property_name}"
        snap = self.snapshot()
        return snap.get(key)

    def summary(self) -> Dict[str, Any]:
        """Return a summary dict of the timeline state."""
        return {
            "active": len(self._running),
            "animations": [
                {
                    "id": ra.animation_id,
                    "target": ra.target_id,
                    "property": ra.property_name,
                    "elapsed_ms": round(ra.elapsed_ms, 1),
                    "duration_ms": ra.duration_ms,
                    "completed": ra.completed,
                }
                for ra in self._running
            ],
        }
