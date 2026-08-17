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
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Contract tables — from the Nyrqis API Registry (NFS-006 / ADR-0025)
# ---------------------------------------------------------------------------

NSTUDIO_SCHEMA_VERSION = "0.4.0"
SUPPORTED_SCHEMA_VERSIONS = ("0.4.0",)

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
        components[type_name] = (
            str(entry.get("category", "")),
            tuple(str(p) for p in entry.get("properties") or []),
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
class NstudioDocument:
    version: str
    project: Dict[str, Any]
    themes: Dict[str, Any]
    states: Dict[str, Any]
    behaviors: List[NstudioBehavior]
    bindings: List[NstudioBinding]
    screens: List[NstudioScreen]

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
        ``$state:key`` arguments substituted from the current document
        state (NFS-001 §7.1: plain substitution, missing keys left as the
        literal placeholder)."""
        behavior = self.behavior_by_id(behavior_id)
        if behavior is None:
            raise NstudioValidationError(
                f"behavior '{behavior_id}' does not exist")
        action = behavior.action
        args: Dict[str, Any] = {}
        for key, value in (action.get("arguments") or {}).items():
            if isinstance(value, str) and value.startswith("$state:"):
                state_key = value[len("$state:"):]
                args[key] = self.states.get(state_key, value)
            else:
                args[key] = value
        return action.get("target", ""), action.get("name", ""), args

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

        def walk(c: NstudioComponent, depth: int) -> None:
            l = c.layout
            bounds = f"({l.get('x', 0)},{l.get('y', 0)} {l.get('width', 0)}x{l.get('height', 0)})"
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
        behaviors=[_parse_behavior(b) for b in raw.get("behaviors") or []],
        bindings=[_parse_binding(b) for b in raw.get("bindings") or []],
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
    return NstudioComponent(
        id=str(raw.get("id", "")),
        type=str(raw.get("type", "")),
        properties=raw.get("properties") or {},
        layout=raw.get("layout") or {},
        events=raw.get("events") or {},
        children=[_parse_component(c) for c in raw.get("children") or []],
    )


# ---------------------------------------------------------------------------
# Validation (NFS-001 §3–§8)
# ---------------------------------------------------------------------------

def _validate(doc: NstudioDocument) -> List[str]:
    issues: List[str] = []

    component_ids = doc.component_ids()
    seen: set = set()
    for cid in component_ids:
        if not cid:
            issues.append("component with empty id")
        elif cid in seen:
            issues.append(f"duplicate component id '{cid}'")
        seen.add(cid)

    for screen in doc.screens:
        _validate_component(screen.root, issues, doc, component_ids)

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
                        doc: NstudioDocument, component_ids: List[str]) -> None:
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

    for key in ("x", "y", "width", "height"):
        value = c.layout.get(key)
        if not isinstance(value, int) or value < 0:
            issues.append(
                f"component '{c.id}': layout '{key}' must be a "
                f"non-negative integer")

    for child in c.children:
        _validate_component(child, issues, doc, component_ids)


def _validate_behavior(behavior: NstudioBehavior, doc: NstudioDocument,
                       issues: List[str], component_ids: List[str]) -> None:
    condition = behavior.condition
    if condition is not None:
        operator = condition.get("operator")
        if operator not in ("equals", "notEquals"):
            issues.append(
                f"behavior '{behavior.id}': condition operator must be "
                f"'equals' or 'notEquals'")
        state_key = condition.get("state")
        if state_key not in doc.states:
            issues.append(
                f"behavior '{behavior.id}': condition references unknown "
                f"state '{state_key}'")

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


def _validate_binding(binding: NstudioBinding, doc: NstudioDocument,
                      issues: List[str], component_ids: List[str]) -> None:
    if binding.component not in component_ids:
        issues.append(
            f"binding: component '{binding.component}' does not exist")
    if binding.state not in doc.states:
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
    "NstudioDocument", "loads", "load",
]
