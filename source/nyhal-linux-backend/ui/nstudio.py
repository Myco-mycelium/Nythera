#!/usr/bin/env python3
"""nstudio — NUI (.nstudio) runtime consumption — reference floor.

Parses, validates, and renders Nyrqis UI Definition documents produced by
NyForge (the visual designer). ``.nstudio`` is the stable intermediate
representation between the editor and any consumer of the design
(NyForge NFS-001 §1); this module is the Nyrqis-side consumer.

Language posture (ADR-0020): the UI/Shell layers are platform layers —
their shipped execution paths must not depend on the Python interpreter.
This module is the **reference implementation** (Python's role: UI
tooling, research/prototyping, above the boundary). The shipped
parse/validate hot path is the Rust crate ``rust/nyui`` behind ABI-001
with its conformance gate (``ui/nstudio_codec.py``), mirroring the
seccomp/transport/ipcd migrations. The conformance bar: this floor's
test suite must pass through the FFI unchanged.

Contract tables are loaded from the **Nyrqis API Registry**
(``ui/contracts/nui-api-v1.json``) — the single machine-readable source
of truth for the NUI component vocabulary (NFS-006, ADR-0025). The Rust
crate (``rust/nyui``) embeds the same registry, and NyForge's C# tables
are regenerated from it. Add a component or action to the registry
first; never edit a consumer's tables by hand (NFC-001 §4.3
anti-drift).

Schema: NFS-001 0.4.0. A document records the schema version it was
written against; a version this module does not understand raises
``NstudioVersionError`` instead of silently misinterpreting the file
(``ProjectSerializer.IsCompatible`` semantics).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import nexpr

# ---------------------------------------------------------------------------
# Contract tables — from the Nyrqis API Registry (NFS-006 / ADR-0025)
# ---------------------------------------------------------------------------

NSTUDIO_SCHEMA_VERSION = "0.4.0"
SUPPORTED_SCHEMA_VERSIONS = ("0.4.0",)

# State scopes (NUI-SCHEMA §8.4): the ``stateScopes`` section maps a
# scope name to its state dictionary. ``global`` is the named form of
# the flat ``states`` section (same precedence — a bare reference
# resolves against the flat section first, then ``global``); ``screen``
# and ``component`` are per-screen/per-component maps (dot-qualified:
# ``state.component.<id>.<key>``); ``session`` and ``persistent`` are
# flat maps referenced as ``state.session.<key>`` / ``state.persistent.<key>``.
STATE_SCOPES = ("global", "screen", "component", "session", "persistent")

# The registry lives next to this module (ui/contracts/nui-api-v1.json).
# Both this floor and the Rust crate (rust/nyui, which embeds the same
# file) read it; NyForge regenerates its C# tables from it. A missing or
# malformed registry is a hard error at import — the tables are never
# silently empty.
_REGISTRY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "contracts", "nui-api-v1.json")


def _load_registry() -> Tuple[
        Dict[str, Tuple[str, Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]],
        Dict[str, Tuple[str, ...]]]:
    """Load the component and system-action tables from the registry.

    Returns ``(COMPONENT_CONTRACTS, SYSTEM_ACTIONS)`` with the historical
    shapes — ``type -> (category, properties, events, instance-actions)``
    and ``name -> argument-names`` — so the rest of this module is
    unchanged.
    """
    try:
        with open(_REGISTRY_PATH, "r", encoding="utf-8") as handle:
            registry = json.load(handle)
    except OSError as exc:
        raise RuntimeError(
            f"cannot load the Nyrqis API Registry at '{_REGISTRY_PATH}': {exc}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Nyrqis API Registry at '{_REGISTRY_PATH}' is malformed JSON: {exc}")

    components: Dict[str, Tuple[str, Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]] = {}
    for entry in registry.get("components") or []:
        type_name = str(entry.get("type", ""))
        if not type_name:
            raise RuntimeError(
                "Nyrqis API Registry: a component entry is missing its 'type'")
        # properties are metadata objects (NFS-006: name/type/default/
        # bindable/required + optional min/max/enumValues/units); the
        # historical table keeps the property NAMES.
        props = entry.get("properties") or []
        names = tuple(
            str(p["name"]) if isinstance(p, dict) else str(p) for p in props)
        components[type_name] = (
            str(entry.get("category", "")),
            names,
            tuple(str(e) for e in entry.get("events") or []),
            tuple(str(a) for a in entry.get("actions") or []),
        )

    system_actions: Dict[str, Tuple[str, ...]] = {}
    for entry in registry.get("systemActions") or []:
        name = str(entry.get("name", ""))
        if not name:
            raise RuntimeError(
                "Nyrqis API Registry: a system-action entry is missing its 'name'")
        system_actions[name] = tuple(str(a) for a in entry.get("arguments") or [])

    return components, system_actions


# type -> (category, properties, events, instance-actions)
COMPONENT_CONTRACTS: Dict[str, Tuple[str, Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]]
# System actions: name -> allowed argument names
SYSTEM_ACTIONS: Dict[str, Tuple[str, ...]]
COMPONENT_CONTRACTS, SYSTEM_ACTIONS = _load_registry()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class NstudioError(Exception):
    """Base class for .nstudio runtime-consumption errors."""


class NstudioVersionError(NstudioError):
    """The document's schema version is not supported (NFS-001 §9)."""


class NstudioValidationError(NstudioError):
    """The document failed validation against the NUI contract tables."""


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

@dataclass
class NstudioComponent:
    """One component node (NFS-001 §3)."""

    id: str
    type: str
    properties: Dict[str, Any] = field(default_factory=dict)
    layout: Dict[str, int] = field(default_factory=dict)
    events: Dict[str, Optional[str]] = field(default_factory=dict)
    children: List["NstudioComponent"] = field(default_factory=list)
    # Reusable-component instance (NFS-006 §9): when component_ref is
    # set, this node references a master in the document's components[]
    # section; overrides apply on top of the master's properties.
    component_ref: Optional[str] = None
    overrides: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NstudioScreen:
    id: str
    size: Dict[str, int]
    root: NstudioComponent


@dataclass
class NstudioBehavior:
    id: str
    condition: Optional[Dict[str, Any]]
    action: Dict[str, Any]


@dataclass
class NstudioBinding:
    component: str
    property: str
    state: str


@dataclass
class NstudioAnimation:
    """One declarative animation (NUI-SCHEMA §8.3): a timed transition
    of one of a target component's properties, triggered by a behavior's
    ``Nyrqis.Animation.Play`` action."""

    id: str
    target: str
    property: str
    duration: int = 300
    delay: int = 0
    easing: str = "ease-in-out"
    repeat: int = 0
    direction: str = "forward"
    # Keyframes (NUI-SCHEMA §8.3): an optional multi-point curve —
    # [{"offset": 0.0–1.0, "value": number|string|boolean}] with
    # strictly increasing offsets. Absent = a single-segment transition.
    keyframes: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class NstudioDocument:
    version: str
    project: Dict[str, Any]
    themes: Dict[str, Any]
    states: Dict[str, Any]
    # State scopes (NUI-SCHEMA §8.4): ``{"screen": {...}, "component":
    # {...}, "session": {...}, "persistent": {...}}`` — references are
    # dotted: ``state.session.foo``. ``global`` is the named form of the
    # flat ``states`` section.
    state_scopes: Dict[str, Any]
    # Localization (NUI-SCHEMA §8.1): ``{"active": "en", "tables":
    # {"en": {"settings.save": "Save"}, ...}}`` — ``$localize:key``
    # string references resolve through the active locale's table.
    locales: Dict[str, Any]
    # Resources (NUI-SCHEMA §8.2): ``{"assets": [{id, kind, path,
    # sha256?}]}`` — ``$asset:id`` string references (e.g. an Image's
    # ``source``) name declared assets.
    resources: Dict[str, Any]
    # Animations (NUI-SCHEMA §8.3): a list of NstudioAnimation — timed
    # property transitions a behavior triggers via the
    # ``Nyrqis.Animation.Play`` system action.
    animations: List[NstudioAnimation]
    behaviors: List[NstudioBehavior]
    bindings: List[NstudioBinding]
    screens: List[NstudioScreen]
    reusable_components: List[NstudioComponent] = field(default_factory=list)

    # ---- helpers ----------------------------------------------------------

    def component_ids(self) -> List[str]:
        """Every component id in the document, in document order."""
        out: List[str] = []

        def walk(c: NstudioComponent) -> None:
            out.append(c.id)
            for child in c.children:
                walk(child)

        for screen in self.screens:
            walk(screen.root)
        return out

    def behavior_by_id(self, behavior_id: str) -> Optional[NstudioBehavior]:
        for b in self.behaviors:
            if b.id == behavior_id:
                return b
        return None

    def find_component(self, component_id: str) -> Optional[NstudioComponent]:
        def walk(c: NstudioComponent) -> Optional[NstudioComponent]:
            if c.id == component_id:
                return c
            for child in c.children:
                found = walk(child)
                if found is not None:
                    return found
            return None

        for screen in self.screens:
            found = walk(screen.root)
            if found is not None:
                return found
        return None

    def resolve_action(self, behavior_id: str) -> Tuple[str, str, Dict[str, Any]]:
        """Resolve a behavior to ``(target, name, arguments)`` with any
        ``$state:key`` and ``$expr:`` arguments substituted from the
        current document state (NFS-001 §7.1/§7.2: plain substitution,
        missing keys left as the literal placeholder)."""
        behavior = self.behavior_by_id(behavior_id)
        if behavior is None:
            raise NstudioValidationError(
                f"behavior '{behavior_id}' does not exist")
        action = behavior.action
        args: Dict[str, Any] = {}
        for key, value in (action.get("arguments") or {}).items():
            if isinstance(value, str) and value.startswith("$state:"):
                state_key = value[len("$state:"):]
                args[key] = self.resolve_state(state_key, value)
            elif isinstance(value, str) and value.startswith("$expr:"):
                expression = value[len("$expr:"):]
                args[key] = nexpr.eval_expr(
                    nexpr.parse(expression), self.resolve_states())
            else:
                args[key] = value
        return action.get("target", ""), action.get("name", ""), args

    def resolve_condition(self, behavior_id: str) -> Optional[bool]:
        """Evaluate a behavior's condition against the current document
        state. Returns ``None`` when the behavior has no condition (its
        action always runs). An ``expression`` condition is evaluated by
        the expression language (NUI-SCHEMA §7.2); the legacy
        ``state/operator/value`` equality form is evaluated here too."""
        behavior = self.behavior_by_id(behavior_id)
        if behavior is None:
            raise NstudioValidationError(
                f"behavior '{behavior_id}' does not exist")
        condition = behavior.condition
        if condition is None:
            return None
        expression = condition.get("expression")
        if isinstance(expression, str):
            return nexpr.eval_expr(
                nexpr.parse(expression), self.resolve_states())
        state_key = condition.get("state")
        operator = condition.get("operator")
        actual = self.resolve_state(state_key)
        expected = condition.get("value")
        equal = actual == expected
        return (not equal) if operator == "notEquals" else equal

    def resolve_state(self, state_key: str, default: Any = None) -> Any:
        """Resolve a state reference. A dotted reference
        ``<scope>.<key>`` resolves through the ``stateScopes`` section
        (NUI-SCHEMA §8.4); a bare key resolves against the flat
        ``states`` section first, then the ``global`` scope. Returns
        ``default`` when the reference doesn't exist."""
        if "." in state_key:
            scope, _, rest = state_key.partition(".")
            scoped = self.state_scopes.get(scope)
            if isinstance(scoped, dict):
                return scoped.get(rest, default)
            return default
        if state_key in self.states:
            return self.states[state_key]
        global_scope = self.state_scopes.get("global")
        if isinstance(global_scope, dict):
            return global_scope.get(state_key, default)
        return default

    def resolve_states(self) -> Dict[str, Any]:
        """The flattened state view used by the expression evaluator:
        flat ``states`` keys merged with every scope's entries under
        their dotted names (``persistent.volume`` etc.), so
        ``state.persistent.volume`` resolves and bare references keep
        working. Flat keys win on collision."""
        merged = dict(self.states)
        for scope, table in self.state_scopes.items():
            if not isinstance(table, dict):
                continue
            for key, value in table.items():
                merged.setdefault(f"{scope}.{key}", value)
        return merged

    def render(self, screen_id: Optional[str] = None) -> List[Tuple[NstudioComponent, int]]:
        """Flatten the given screen's component tree into depth-ordered
        ``(component, depth)`` entries with absolute layout coordinates.
        This is the runtime-facing shape a shell walks to lay out the
        design; coordinates are absolute canvas coordinates as authored
        (NFS-001 §3 ``layout``)."""
        screen = self._screen(screen_id)

        entries: List[Tuple[NstudioComponent, int]] = []

        def walk(c: NstudioComponent, depth: int) -> None:
            entries.append((c, depth))
            for child in c.children:
                walk(child, depth + 1)

        walk(screen.root, 0)
        return entries

    def text_preview(self, screen_id: Optional[str] = None) -> str:
        """A deterministic, dependency-free text rendering of the screen
        tree (the stand-in renderer, like NyForge's Preview placeholder):
        one line per component with type, id, and absolute bounds."""
        screen = self._screen(screen_id)
        lines = [f"screen {screen.id} {screen.size.get('width')}x{screen.size.get('height')}"]

        size = screen.size
        cw, ch = size.get("width", 0), size.get("height", 0)

        def walk(c: NstudioComponent, depth: int) -> None:
            r = resolve_layout(c.layout, cw, ch)
            bounds = f"({r['x']},{r['y']} {r['width']}x{r['height']})"
            lines.append("  " * depth + f"{c.type} {c.id} {bounds}")
            for child in c.children:
                walk(child, depth + 1)

        walk(screen.root, 0)
        return "\n".join(lines)

    def _screen(self, screen_id: Optional[str]) -> NstudioScreen:
        if screen_id is not None:
            for screen in self.screens:
                if screen.id == screen_id:
                    return screen
            raise NstudioValidationError(f"screen '{screen_id}' does not exist")
        if not self.screens:
            raise NstudioValidationError("document has no screens")
        return self.screens[0]


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def loads(text: str) -> NstudioDocument:
    """Parse + validate a .nstudio document from a string. Raises
    ``NstudioVersionError`` on an unsupported schema version and
    ``NstudioValidationError`` on any contract violation."""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise NstudioValidationError(f"malformed JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise NstudioValidationError("document root must be a JSON object")
    return _from_dict(raw)


def load(path: os.PathLike | str) -> NstudioDocument:
    with open(path, "r", encoding="utf-8") as handle:
        return loads(handle.read())


def _from_dict(raw: Dict[str, Any]) -> NstudioDocument:
    version = raw.get("version")
    if version != NSTUDIO_SCHEMA_VERSION:
        raise NstudioVersionError(
            f"unsupported schema version '{version}'; supported: "
            f"{', '.join(SUPPORTED_SCHEMA_VERSIONS)}")

    doc = NstudioDocument(
        version=version,
        project=raw.get("project") or {},
        themes=raw.get("themes") or {},
        states=raw.get("states") or {},
        state_scopes=raw.get("stateScopes") or {},
        locales=raw.get("locales") or {},
        resources=raw.get("resources") or {},
        animations=[_parse_animation(a) for a in raw.get("animations") or []],
        behaviors=[_parse_behavior(b) for b in raw.get("behaviors") or []],
        bindings=[_parse_binding(b) for b in raw.get("bindings") or []],
        reusable_components=[
            _parse_component(c) for c in raw.get("components") or []],
        screens=[_parse_screen(s) for s in raw.get("screens") or []],
    )

    issues = _validate(doc)
    if issues:
        raise NstudioValidationError("; ".join(issues))
    return doc


def _parse_behavior(raw: Any) -> NstudioBehavior:
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
        raise NstudioValidationError("behavior entries must be objects with a string 'id'")
    action = raw.get("action")
    if not isinstance(action, dict) or not isinstance(action.get("name"), str):
        raise NstudioValidationError(
            f"behavior '{raw['id']}' action must declare a 'name'")
    condition = raw.get("condition")
    if condition is not None and not isinstance(condition, dict):
        raise NstudioValidationError(
            f"behavior '{raw['id']}' condition must be null or an object")
    return NstudioBehavior(id=raw["id"], condition=condition, action=action)


def _parse_animation(raw: Any) -> NstudioAnimation:
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
        raise NstudioValidationError(
            "animation entries must be objects with a string 'id'")
    keyframes_raw = raw.get("keyframes")
    if keyframes_raw is None:
        keyframes: List[Dict[str, Any]] = []
    elif isinstance(keyframes_raw, list):
        keyframes = keyframes_raw
    else:
        raise NstudioValidationError(
            f"animation '{raw['id']}': keyframes must be a list")
    return NstudioAnimation(
        id=raw["id"],
        target=str(raw.get("target", "")),
        property=str(raw.get("property", "")),
        duration=int(raw.get("duration", 300)),
        delay=int(raw.get("delay", 0)),
        easing=str(raw.get("easing", "ease-in-out")),
        repeat=int(raw.get("repeat", 0)),
        direction=str(raw.get("direction", "forward")),
        keyframes=keyframes,
    )


def _parse_binding(raw: Any) -> NstudioBinding:
    if not isinstance(raw, dict):
        raise NstudioValidationError("binding entries must be objects")
    return NstudioBinding(
        component=str(raw.get("component", "")),
        property=str(raw.get("property", "")),
        state=str(raw.get("state", "")),
    )


def _parse_screen(raw: Any) -> NstudioScreen:
    if not isinstance(raw, dict):
        raise NstudioValidationError("screen entries must be objects")
    size = raw.get("size") or {}
    root = raw.get("root")
    if not isinstance(root, dict):
        raise NstudioValidationError(
            f"screen '{raw.get('id')}' must declare a 'root' component")
    return NstudioScreen(
        id=str(raw.get("id", "")),
        size=size if isinstance(size, dict) else {},
        root=_parse_component(root),
    )


def _parse_component(raw: Any) -> NstudioComponent:
    if not isinstance(raw, dict):
        raise NstudioValidationError("component nodes must be objects")
    component_ref = raw.get("componentRef")
    return NstudioComponent(
        id=str(raw.get("id", "")),
        # A reusable-component instance (NFS-006 §9) omits 'type' — its
        # contract is the referenced master's, resolved during
        # validation (a node declaring both is rejected).
        type=str(raw.get("type", "")),
        properties=raw.get("properties") or {},
        layout=raw.get("layout") or {},
        events=raw.get("events") or {},
        children=[_parse_component(c) for c in raw.get("children") or []],
        component_ref=str(component_ref) if component_ref else None,
        overrides=raw.get("overrides") or {},
    )


# ---------------------------------------------------------------------------
# Validation (NFS-001 §3–§8)
# ---------------------------------------------------------------------------

def _validate(doc: NstudioDocument) -> List[str]:
    issues: List[str] = []

    # Resources section (NUI-SCHEMA §8.2): if present, it must be
    # {"assets": [{id, kind, path, sha256?}]} with unique ids, an
    # allowed kind, and a non-empty path.
    resources = doc.resources or {}
    if resources:
        if not isinstance(resources, dict) or not isinstance(
                resources.get("assets"), list):
            issues.append(
                "resources section must declare an 'assets' list")
        else:
            asset_ids: set = set()
            for asset in resources["assets"]:
                if not isinstance(asset, dict):
                    issues.append("resource entries must be objects")
                    continue
                aid = asset.get("id")
                if not isinstance(aid, str) or not aid:
                    issues.append("resource entries must declare a string 'id'")
                elif aid in asset_ids:
                    issues.append(f"duplicate resource id '{aid}'")
                asset_ids.add(aid)
                kind = asset.get("kind")
                if kind not in ASSET_KINDS:
                    issues.append(
                        f"resource '{aid}': kind '{kind}' not in "
                        f"{sorted(ASSET_KINDS)}")
                path = asset.get("path")
                if not isinstance(path, str) or not path:
                    issues.append(
                        f"resource '{aid}': must declare a non-empty 'path'")
                sha = asset.get("sha256")
                if sha is not None and (
                        not isinstance(sha, str) or len(sha) != 64
                        or not all(c in "0123456789abcdef" for c in sha)):
                    issues.append(
                        f"resource '{aid}': 'sha256' must be a 64-char hex "
                        f"string")

    # State scopes section (NUI-SCHEMA §8.4): every scope name must be
    # one of the known scopes and hold an object.
    state_scopes = doc.state_scopes or {}
    if state_scopes:
        if not isinstance(state_scopes, dict):
            issues.append("stateScopes section must be an object")
            state_scopes = {}
        for scope_name, table in state_scopes.items():
            if scope_name not in STATE_SCOPES:
                issues.append(
                    f"stateScopes: unknown scope '{scope_name}'")
            elif not isinstance(table, dict):
                issues.append(
                    f"stateScopes: scope '{scope_name}' must be an object")

    # Animations section (NUI-SCHEMA §8.3): a list of declarative
    # animations — unique ids, a target that names a component, a
    # non-empty property, and validated timing parameters.
    ANIM_EASINGS = ("linear", "ease-in", "ease-out", "ease-in-out",
                    "steps")
    ANIM_DIRECTIONS = ("forward", "reverse", "alternate")
    animation_ids: set = set()
    for anim in doc.animations:
        if not anim.id:
            issues.append("animation with empty id")
            continue
        if anim.id in animation_ids:
            issues.append(f"duplicate animation id '{anim.id}'")
        animation_ids.add(anim.id)
        if not anim.property:
            issues.append(f"animation '{anim.id}': must declare a 'property'")
        for key in ("duration", "delay", "repeat"):
            value = getattr(anim, key)
            if not isinstance(value, int) or value < 0:
                issues.append(
                    f"animation '{anim.id}': '{key}' must be a "
                    f"non-negative integer")
        if anim.easing not in ANIM_EASINGS:
            issues.append(
                f"animation '{anim.id}': easing '{anim.easing}' not in "
                f"{sorted(ANIM_EASINGS)}")
        if anim.direction not in ANIM_DIRECTIONS:
            issues.append(
                f"animation '{anim.id}': direction '{anim.direction}' not in "
                f"{sorted(ANIM_DIRECTIONS)}")
        # Keyframes (NUI-SCHEMA §8.3): optional multi-point curve — each
        # keyframe has a numeric offset in [0, 1] and a value, and the
        # offsets must be strictly increasing (the runtime interpolates
        # between them; the crate mirrors these messages byte-for-byte).
        prev_offset: Optional[float] = None
        for idx, kf in enumerate(anim.keyframes):
            if not isinstance(kf, dict):
                issues.append(
                    f"animation '{anim.id}': keyframe {idx} must be "
                    f"an object")
                continue
            offset = kf.get("offset")
            if not isinstance(offset, (int, float)) \
                    or isinstance(offset, bool) \
                    or not (0.0 <= offset <= 1.0):
                issues.append(
                    f"animation '{anim.id}': keyframe {idx} 'offset' must "
                    f"be a number in [0, 1]")
            elif prev_offset is not None and offset <= prev_offset:
                issues.append(
                    f"animation '{anim.id}': keyframe {idx} 'offset' must "
                    f"be greater than the previous offset")
            else:
                prev_offset = float(offset)
            value = kf.get("value")
            if value is None or isinstance(value, (dict, list)):
                issues.append(
                    f"animation '{anim.id}': keyframe {idx} 'value' must "
                    f"be a number, string, or boolean")

    # Localization section (NUI-SCHEMA §8.1): if present, it must be
    # {"active": str, "tables": {locale: {key: str}}} and the active
    # locale must have a table.
    locales = doc.locales or {}
    if locales:
        active = locales.get("active")
        tables = locales.get("tables")
        if not isinstance(active, str) or not isinstance(tables, dict):
            issues.append(
                "locales section must declare a string 'active' and a "
                "'tables' object")
        else:
            for locale_name, table in tables.items():
                if not isinstance(table, dict) or not all(
                        isinstance(k, str) and isinstance(v, str)
                        for k, v in table.items()):
                    issues.append(
                        f"locale '{locale_name}' table must map string "
                        f"keys to string values")
            if active not in tables:
                issues.append(
                    f"locales: active locale '{active}' has no table")

    component_ids = doc.component_ids()
    for anim in doc.animations:
        if anim.target and anim.target not in component_ids:
            issues.append(
                f"animation '{anim.id}': target '{anim.target}' does "
                f"not exist")

    scoped_states = {}
    for scope, table in state_scopes.items():
        if isinstance(table, dict):
            for key in table:
                scoped_states[f"{scope}.{key}"] = True

    seen: set = set()
    for cid in component_ids:
        if not cid:
            issues.append("component with empty id")
        elif cid in seen:
            issues.append(f"duplicate component id '{cid}'")
        seen.add(cid)

    # Reusable-component masters (NFS-006 §9): validate the master tree
    # like any component (its properties/events must fit its contract),
    # and its id must be unique among all component ids.
    master_ids: set = set()
    for master in doc.reusable_components:
        if not master.id:
            issues.append("reusable component with empty id")
            continue
        if master.id in master_ids or master.id in seen:
            issues.append(f"duplicate reusable component id '{master.id}'")
        master_ids.add(master.id)
        _validate_component(master, issues, doc, component_ids)

    for screen in doc.screens:
        _validate_component(screen.root, issues, doc, component_ids, master_ids)

    behavior_ids: set = set()
    for behavior in doc.behaviors:
        if not behavior.id:
            issues.append("behavior with empty id")
            continue
        if behavior.id in behavior_ids:
            issues.append(f"duplicate behavior id '{behavior.id}'")
        behavior_ids.add(behavior.id)
        _validate_behavior(behavior, doc, issues, component_ids)

    for binding in doc.bindings:
        _validate_binding(binding, doc, issues, component_ids)

    return issues


def _validate_component(c: NstudioComponent, issues: List[str],
                        doc: NstudioDocument, component_ids: List[str],
                        master_ids: Optional[set] = None) -> None:
    master_ids = master_ids or set()
    # Reusable-component instance (NFS-006 §9): the ref must name a
    # master in components[], and its contract is the master's type —
    # overrides are the only properties an instance may carry.
    if c.component_ref is not None:
        if c.type:
            issues.append(
                f"component '{c.id}': reusable instance must not "
                f"declare its own type")
        if c.component_ref not in master_ids:
            issues.append(
                f"component '{c.id}': componentRef '{c.component_ref}' "
                f"does not name a reusable component")
            return
        master = next(
            (m for m in doc.reusable_components
             if m.id == c.component_ref), None)
        if master is not None:
            contract = COMPONENT_CONTRACTS.get(master.type)
            if contract is None:
                issues.append(
                    f"component '{c.id}': componentRef '{c.component_ref}' "
                    f"has unknown type '{master.type}'")
                return
            _category, properties, events, _actions = contract
            for key in c.overrides:
                if key not in properties:
                    issues.append(
                        f"component '{c.id}': override '{key}' not in "
                        f"the '{master.type}' contract")
            for value in c.overrides.values():
                _check_localize_ref(
                    value, doc, issues,
                    f"component '{c.id}' override")
                _check_asset_ref(
                    value, doc, issues,
                    f"component '{c.id}' override")
                _check_expr_ref(
                    value, doc, issues,
                    f"component '{c.id}' override")
            for event, behavior_id in c.events.items():
                if event not in events:
                    issues.append(
                        f"component '{c.id}': event '{event}' not in "
                        f"the '{master.type}' contract")
                elif behavior_id is not None and \
                        behavior_id not in {b.id for b in doc.behaviors}:
                    issues.append(
                        f"component '{c.id}': event '{event}' references "
                        f"unknown behavior '{behavior_id}'")
        _check_layout(c, issues)
        return

    contract = COMPONENT_CONTRACTS.get(c.type)
    if contract is None:
        issues.append(f"component '{c.id}': unknown type '{c.type}'")
    else:
        _category, properties, events, _actions = contract
        for key in c.properties:
            if key not in properties:
                issues.append(
                    f"component '{c.id}': property '{key}' not in the "
                    f"'{c.type}' contract")
        for value in c.properties.values():
            _check_localize_ref(
                value, doc, issues, f"component '{c.id}' property")
            _check_asset_ref(
                value, doc, issues, f"component '{c.id}' property")
            _check_expr_ref(
                value, doc, issues, f"component '{c.id}' property")
        for event, behavior_id in c.events.items():
            if event not in events:
                issues.append(
                    f"component '{c.id}': event '{event}' not in the "
                    f"'{c.type}' contract")
            elif behavior_id is not None and \
                    behavior_id not in {b.id for b in doc.behaviors}:
                issues.append(
                    f"component '{c.id}': event '{event}' references "
                    f"unknown behavior '{behavior_id}'")

    _check_layout(c, issues)

    for child in c.children:
        _validate_component(child, issues, doc, component_ids, master_ids)


def _check_layout(c: NstudioComponent, issues: List[str]) -> None:
    for key in ("x", "y", "width", "height"):
        value = c.layout.get(key)
        if not isinstance(value, int) or value < 0:
            issues.append(
                f"component '{c.id}': layout '{key}' must be a "
                f"non-negative integer")
    # Responsive constraints (NUI-SCHEMA §4): bounds and ratios are
    # validated the same way on both gates (differential-tested).
    for key in ("minWidth", "maxWidth", "minHeight", "maxHeight"):
        value = c.layout.get(key)
        if value is None:
            continue
        if not isinstance(value, int) or value < 0:
            issues.append(
                f"component '{c.id}': layout '{key}' must be a "
                f"non-negative integer")
    for dim in ("Width", "Height"):
        lo, hi = c.layout.get(f"min{dim}"), c.layout.get(f"max{dim}")
        if lo is not None and hi is not None and isinstance(lo, int) \
                and isinstance(hi, int) and lo > hi:
            issues.append(
                f"component '{c.id}': layout 'min{dim}' must be <= "
                f"'max{dim}'")
    ratio = c.layout.get("aspectRatio")
    if ratio is not None and (not isinstance(ratio, (int, float))
                              or isinstance(ratio, bool) or ratio <= 0):
        issues.append(
            f"component '{c.id}': layout 'aspectRatio' must be a "
            f"positive number")
    for key in ("anchorLeft", "anchorTop", "anchorRight", "anchorBottom"):
        value = c.layout.get(key)
        if value is not None and not isinstance(value, bool):
            issues.append(
                f"component '{c.id}': layout '{key}' must be a boolean")


def resolve_layout(layout: Dict[str, Any], container_w: int,
                   container_h: int) -> Dict[str, int]:
    """Apply responsive layout constraints (NUI-SCHEMA §4) to an authored
    layout and return the effective ``x/y/width/height`` for a container
    of the given size — the shape a shell actually lays out.

    Rules:
    - All anchors default **false** — a document without constraint
      fields keeps its absolute authored coordinates exactly as before.
    - ``anchorLeft`` fixes the left edge at ``x``; ``anchorRight`` fixes
      the right edge at ``container_w - x`` (so ``x`` doubles as the
      right inset). **Both together** make the width stretch:
      ``width = container_w - 2*x``, clamped to min/max width.
    - Vertical is the mirror: ``anchorTop``/``anchorBottom`` with ``y``
      as the top/bottom inset; both together make the height stretch.
    - ``minWidth``/``maxWidth``/``minHeight``/``maxHeight`` clamp the
      computed (or authored) size.
    - ``aspectRatio`` derives the non-stretched dimension when one axis
      stretches; otherwise the authored size stands (the designer
      chose it)."""
    x = layout.get("x", 0)
    y = layout.get("y", 0)
    w = layout.get("width", 0)
    h = layout.get("height", 0)
    min_w = layout.get("minWidth")
    max_w = layout.get("maxWidth")
    min_h = layout.get("minHeight")
    max_h = layout.get("maxHeight")
    ratio = layout.get("aspectRatio")

    anchor_l = layout.get("anchorLeft", False)
    anchor_r = layout.get("anchorRight", False)
    anchor_t = layout.get("anchorTop", False)
    anchor_b = layout.get("anchorBottom", False)

    stretch_w = bool(anchor_l and anchor_r)
    stretch_h = bool(anchor_t and anchor_b)

    if stretch_w:
        w = container_w - 2 * x
    elif anchor_r:
        x = container_w - x - w
    if stretch_h:
        h = container_h - 2 * y
    elif anchor_b:
        y = container_h - y - h

    # Aspect ratio derives the non-stretched axis (width-driven when
    # both stretch).
    if ratio is not None and ratio > 0:
        if stretch_w and not stretch_h:
            h = int(w / ratio)
        elif stretch_h and not stretch_w:
            w = int(h * ratio)

    if min_w is not None:
        w = max(w, min_w)
    if max_w is not None:
        w = min(w, max_w)
    if min_h is not None:
        h = max(h, min_h)
    if max_h is not None:
        h = min(h, max_h)

    return {"x": x, "y": y, "width": w, "height": h}


def resolve_text(text: str, locales: Dict[str, Any]) -> str:
    """Resolve ``$localize:key`` references through the active locale's
    table (NUI-SCHEMA §8.1). A missing key is left as the literal
    placeholder (fail-soft at resolution — the import gate rejects
    missing keys up front). Plain text with no references is returned
    unchanged."""
    if "$localize:" not in text or not locales:
        return text
    active = locales.get("active")
    table = (locales.get("tables") or {}).get(active) or {}
    out = text
    for key, value in table.items():
        out = out.replace(f"$localize:{key}", value)
    return out


ASSET_KINDS = ("image", "svg", "icon", "font", "audio", "video",
               "material", "animation")


def _check_asset_ref(value: Any, doc: NstudioDocument, issues: List[str],
                     where: str) -> None:
    """A ``$asset:id`` reference must name a declared resource in the
    document's ``resources.assets`` (fail-closed, like every other
    dangling reference)."""
    if not isinstance(value, str) or "$asset:" not in value:
        return
    resources = doc.resources or {}
    assets = resources.get("assets") if isinstance(resources, dict) else None
    declared = {a.get("id") for a in assets} if isinstance(assets, list) else set()
    if not declared:
        issues.append(
            f"{where}: '$asset:' reference requires a 'resources' "
            f"section with an 'assets' list")
        return
    for key in re.findall(r"\$asset:([A-Za-z0-9_.-]+)", value):
        if key not in declared:
            issues.append(
                f"{where}: asset '{key}' is not declared in resources")


def _check_localize_ref(value: Any, doc: NstudioDocument, issues: List[str],
                        where: str) -> None:
    """A ``$localize:key`` reference must exist in the ACTIVE locale's
    table (fail-closed at the import gate, like every other dangling
    reference). Missing/empty locales section is itself an error."""
    if not isinstance(value, str) or "$localize:" not in value:
        return
    locales = doc.locales
    tables = locales.get("tables") if isinstance(locales, dict) else None
    active = locales.get("active") if isinstance(locales, dict) else None
    if not isinstance(tables, dict) or not isinstance(active, str):
        issues.append(
            f"{where}: '$localize:' reference requires a 'locales' "
            f"section with 'active' and 'tables'")
        return
    table = tables.get(active)
    if not isinstance(table, dict):
        issues.append(
            f"{where}: locale '{active}' has no table")
        return
    for key in re.findall(r"\$localize:([A-Za-z0-9_.-]+)", value):
        if key not in table:
            issues.append(
                f"{where}: localize key '{key}' not in locale '{active}'")


def _state_known(state_key: Any, doc: NstudioDocument) -> bool:
    """A state reference exists when it is a flat key or a dotted
    reference into a declared scope (NUI-SCHEMA §8.4)."""
    if not isinstance(state_key, str) or not state_key:
        return False
    if "." in state_key:
        scope, _, rest = state_key.partition(".")
        table = (doc.state_scopes or {}).get(scope)
        return isinstance(table, dict) and rest in table
    if state_key in doc.states:
        return True
    global_scope = (doc.state_scopes or {}).get("global")
    return isinstance(global_scope, dict) and state_key in global_scope


def _scoped_state_keys(doc: NstudioDocument) -> set:
    """Every dotted ``scope.key`` name declared in stateScopes."""
    keys: set = set()
    for scope, table in (doc.state_scopes or {}).items():
        if isinstance(table, dict):
            for key in table:
                keys.add(f"{scope}.{key}")
    return keys


def _validate_behavior(behavior: NstudioBehavior, doc: NstudioDocument,
                       issues: List[str], component_ids: List[str]) -> None:
    condition = behavior.condition
    if condition is not None:
        expression = condition.get("expression")
        if expression is not None:
            # Expression conditions (NUI-SCHEMA §7.2) supersede the
            # legacy state/operator/value equality form.
            if not isinstance(expression, str):
                issues.append(
                    f"behavior '{behavior.id}': condition expression must "
                    f"be a string")
            else:
                try:
                    node = nexpr.parse(expression)
                except nexpr.ExprError as exc:
                    issues.append(
                        f"behavior '{behavior.id}' condition expression: "
                        f"{exc}")
                else:
                    problem = nexpr.validate(node, _scoped_state_keys(doc) | set(doc.states))
                    if problem is not None:
                        issues.append(
                            f"behavior '{behavior.id}' condition "
                            f"expression: {problem}")
        else:
            operator = condition.get("operator")
            if operator not in ("equals", "notEquals"):
                issues.append(
                    f"behavior '{behavior.id}': condition operator must be "
                    f"'equals' or 'notEquals'")
            state_key = condition.get("state")
            if not _state_known(state_key, doc):
                issues.append(
                    f"behavior '{behavior.id}': condition references "
                    f"unknown state '{state_key}'")

    target = behavior.action.get("target")
    name = behavior.action.get("name")
    if target == "System":
        allowed = SYSTEM_ACTIONS.get(name)
        if allowed is None:
            issues.append(
                f"behavior '{behavior.id}': unknown system action '{name}'")
        else:
            for arg in (behavior.action.get("arguments") or {}):
                if arg not in allowed:
                    issues.append(
                        f"behavior '{behavior.id}': argument '{arg}' not "
                        f"in the '{name}' contract")
            if name == "Nyrqis.Animation.Play":
                # The animation reference must name a declared animation
                # (NUI-SCHEMA §8.3) — fail-closed like every other
                # dangling reference.
                anim_id = (behavior.action.get("arguments") or {}).get(
                    "animation")
                if anim_id not in {a.id for a in doc.animations}:
                    issues.append(
                        f"behavior '{behavior.id}': animation "
                        f"'{anim_id}' is not declared in 'animations'")
    elif target in component_ids:
        component = doc.find_component(target)
        contract = COMPONENT_CONTRACTS.get(component.type) if component else None
        actions = contract[3] if contract else ()
        if name not in actions:
            issues.append(
                f"behavior '{behavior.id}': action '{name}' not declared "
                f"by component '{target}'")
    else:
        issues.append(
            f"behavior '{behavior.id}': action target '{target}' is "
            f"neither 'System' nor a component id")

    for value in (behavior.action.get("arguments") or {}).values():
        _check_localize_ref(
            value, doc, issues, f"behavior '{behavior.id}' argument")
        _check_expr_ref(
            value, doc, issues, f"behavior '{behavior.id}' argument")


def _check_expr_ref(value: Any, doc: NstudioDocument, issues: List[str],
                    where: str) -> None:
    """An expression-valued reference (NUI-SCHEMA §7.2) must parse, use
    only known functions with correct arity, and reference only states
    that exist in the document (fail-closed at the import gate).
    Expressions appear as whole-string ``$expr:...`` values (in action
    arguments, properties, and overrides) and as a condition's
    ``expression`` field."""
    # Only whole-string ``$expr:`` values are expressions; the rest are
    # ordinary values (literals, ``$state:``/``$localize:`` refs, ...).
    if not isinstance(value, str) or not value.startswith("$expr:"):
        return
    expression = value[len("$expr:"):]
    try:
        node = nexpr.parse(expression)
    except nexpr.ExprError as exc:
        issues.append(f"{where}: {exc}")
        return
    problem = nexpr.validate(node, _scoped_state_keys(doc) | set(doc.states))
    if problem is not None:
        issues.append(f"{where}: {problem}")


def _validate_binding(binding: NstudioBinding, doc: NstudioDocument,
                      issues: List[str], component_ids: List[str]) -> None:
    if binding.component not in component_ids:
        issues.append(
            f"binding: component '{binding.component}' does not exist")
    if not _state_known(binding.state, doc):
        issues.append(
            f"binding: state '{binding.state}' does not exist")
    component = doc.find_component(binding.component)
    if component is not None:
        contract = COMPONENT_CONTRACTS.get(component.type)
        properties = contract[1] if contract else ()
        if binding.property not in properties:
            issues.append(
                f"binding: property '{binding.property}' not in the "
                f"'{component.type}' contract for '{binding.component}'")


__all__ = [
    "NSTUDIO_SCHEMA_VERSION", "SUPPORTED_SCHEMA_VERSIONS",
    "COMPONENT_CONTRACTS", "SYSTEM_ACTIONS",
    "NstudioError", "NstudioVersionError", "NstudioValidationError",
    "NstudioComponent", "NstudioScreen", "NstudioBehavior", "NstudioBinding",
    "NstudioAnimation", "NstudioDocument", "loads", "load", "resolve_text",
    "nexpr", "STATE_SCOPES",
]
