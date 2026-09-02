"""NUI Accessibility Schema (NUI-SCHEMA §9).

ARIA-like accessibility model that travels from Nyforge → NUI → Nyrqis
Runtime → OS accessibility system.  Every NUI component can optionally
declare accessibility metadata; the validator checks correctness and
the runtime exports it to the platform.

Roles follow WAI-ARIA 1.2 naming for familiarity; the NUI subset is
small and deterministic so the same validation runs identically in
Nyforge (design time), the Python floor (this file), and the Rust
``nyui`` crate (shipped hot path, ADR-0025).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Roles (WAI-ARIA 1.2 subset relevant to NUI)
# ---------------------------------------------------------------------------

class A11yRole(str, Enum):
    """Accessibility roles for NUI components.

    The subset covers the roles that a desktop shell actually needs.
    Interactive roles are union of widget + landmark roles.
    """

    # Widget roles
    BUTTON = "button"
    CHECKBOX = "checkbox"
    LINK = "link"
    RADIO = "radio"
    SLIDER = "slider"
    SWITCH = "switch"
    TEXTBOX = "textbox"
    MENU = "menu"
    MENUITEM = "menuitem"
    TAB = "tab"
    TABLIST = "tablist"
    TREE = "tree"
    TREEITEM = "treeitem"
    COMBOBOX = "combobox"
    LISTBOX = "listbox"
    OPTION = "option"
    PROGRESSBAR = "progressbar"
    SCROLLBAR = "scrollbar"
    SEPARATOR = "separator"
    TOOLBAR = "toolbar"
    TOOLTIP = "tooltip"
    IMAGE = "image"
    FIGURE = "figure"
    GRID = "grid"
    DIALOG = "dialog"

    # Landmark roles
    BANNER = "banner"
    NAVIGATION = "navigation"
    MAIN = "main"
    COMPLEMENTARY = "complementary"
    CONTENTINFO = "contentinfo"
    REGION = "region"
    FORM = "form"
    SEARCH = "search"

    # Live-region roles
    ALERT = "alert"
    STATUS = "status"
    LOG = "log"
    TIMER = "timer"

    # Document structure
    HEADING = "heading"
    LIST = "list"
    LISTITEM = "listitem"
    PARAGRAPH = "paragraph"
    GROUP = "group"
    PRESENTATION = "presentation"  # explicit "no semantics"
    NONE = "none"                  # alias for presentation


# Roles that must have an accessible name (ARIA requirement)
_ROLES_REQUIRING_NAME = {
    A11yRole.BUTTON, A11yRole.CHECKBOX, A11yRole.LINK, A11yRole.RADIO,
    A11yRole.SLIDER, A11yRole.SWITCH, A11yRole.TEXTBOX, A11yRole.MENUITEM,
    A11yRole.TAB, A11yRole.TREEITEM, A11yRole.COMBOBOX, A11yRole.LISTBOX,
    A11yRole.OPTION, A11yRole.IMAGE, A11yRole.HEADING,
}

# Roles that imply focusable
_ROLES_IMPLYING_FOCUS = {
    A11yRole.BUTTON, A11yRole.CHECKBOX, A11yRole.LINK, A11yRole.RADIO,
    A11yRole.SLIDER, A11yRole.SWITCH, A11yRole.TEXTBOX, A11yRole.TAB,
    A11yRole.TREEITEM, A11yRole.COMBOBOX, A11yRole.MENUITEM,
}


# ---------------------------------------------------------------------------
# Component → default role mapping (NUI-SCHEMA §9.1)
# ---------------------------------------------------------------------------

_DEFAULT_ROLES: Dict[str, A11yRole] = {
    "Button": A11yRole.BUTTON,
    "Checkbox": A11yRole.CHECKBOX,
    "Radio": A11yRole.RADIO,
    "Toggle": A11yRole.SWITCH,
    "Slider": A11yRole.SLIDER,
    "Input": A11yRole.TEXTBOX,
    "PasswordField": A11yRole.TEXTBOX,
    "Link": A11yRole.LINK,
    "Text": A11yRole.NONE,
    "Icon": A11yRole.IMAGE,
    "Image": A11yRole.IMAGE,
    "ProgressBar": A11yRole.PROGRESSBAR,
    "List": A11yRole.LIST,
    "ListItem": A11yRole.LISTITEM,
    "Menu": A11yRole.MENU,
    "MenuItem": A11yRole.MENUITEM,
    "Tabs": A11yRole.TABLIST,
    "TreeView": A11yRole.TREE,
    "DataTable": A11yRole.GRID,
    "Sidebar": A11yRole.COMPLEMENTARY,
    "NavigationRail": A11yRole.NAVIGATION,
    "Toolbar": A11yRole.TOOLBAR,
    "Notification": A11yRole.ALERT,
    "Window": A11yRole.REGION,
    "Dialog": A11yRole.DIALOG,
    "Card": A11yRole.REGION,
    "Panel": A11yRole.REGION,
    "Taskbar": A11yRole.BANNER,
    "StartMenu": A11yRole.MENU,
    "SystemTray": A11yRole.REGION,
    "NotificationCenter": A11yRole.COMPLEMENTARY,
    "Search": A11yRole.SEARCH,
    "LockScreen": A11yRole.DIALOG,
    "ContextMenu": A11yRole.MENU,
    "CommandPalette": A11yRole.DIALOG,
    "PowerMenu": A11yRole.DIALOG,
    "Menu": A11yRole.MENU,
    "ScrollView": A11yRole.GROUP,
    "Container": A11yRole.GROUP,
    "Stack": A11yRole.GROUP,
    "Grid": A11yRole.GROUP,
    "Form": A11yRole.FORM,
}


# ---------------------------------------------------------------------------
# Accessibility metadata
# ---------------------------------------------------------------------------

@dataclass
class A11yMetadata:
    """Accessibility metadata for a single NUI component (NUI-SCHEMA §9).

    All fields are optional; the validator fills in defaults from the
    component type and only flags problems that can't be inferred.
    """

    role: Optional[str] = None
    label: Optional[str] = None          # accessible name
    description: Optional[str] = None    # accessible description
    keyboard_focusable: Optional[bool] = None
    tab_index: Optional[int] = None
    disabled: Optional[bool] = None
    live_region: Optional[str] = None    # "polite" | "assertive" | "off"
    shortcut: Optional[str] = None       # keyboard shortcut hint
    high_contrast: Optional[bool] = None  # high-contrast mode override

    # ---- helpers ----

    def effective_role(self, component_type: str) -> A11yRole:
        """Return the role, falling back to the component's default."""
        if self.role is not None:
            try:
                return A11yRole(self.role)
            except ValueError:
                pass
        default = _DEFAULT_ROLES.get(component_type)
        return default or A11yRole.NONE

    def effective_focusable(self, component_type: str) -> bool:
        """Return whether the component is keyboard-focusable."""
        if self.keyboard_focusable is not None:
            return self.keyboard_focusable
        role = self.effective_role(component_type)
        return role in _ROLES_IMPLYING_FOCUS

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-friendly dict (omitting None values)."""
        d: Dict[str, Any] = {}
        for k in ("role", "label", "description", "keyboard_focusable",
                   "tab_index", "disabled", "live_region", "shortcut",
                   "high_contrast"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "A11yMetadata":
        """Deserialize from a JSON dict."""
        return cls(
            role=d.get("role"),
            label=d.get("label"),
            description=d.get("description"),
            keyboard_focusable=d.get("keyboard_focusable"),
            tab_index=d.get("tab_index"),
            disabled=d.get("disabled"),
            live_region=d.get("live_region"),
            shortcut=d.get("shortcut"),
            high_contrast=d.get("high_contrast"),
        )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_a11y(
    a11y: A11yMetadata,
    component_type: str,
    component_id: str,
) -> List[str]:
    """Validate accessibility metadata for a component.

    Returns a list of warning/error strings.  Empty = valid.

    Error-level issues (block preview per NFS-005):
    - invalid role
    - role requires a label but none provided

    Warning-level issues (surfaced but never block):
    - missing role (will be inferred)
    - missing label on focusable component
    - invalid live_region value
    - tabindex on non-focusable component
    """

    issues: List[str] = []
    prefix = f"a11y[{component_id}]"

    # --- role validation ---
    effective = a11y.effective_role(component_type)

    if a11y.role is not None:
        try:
            A11yRole(a11y.role)
        except ValueError:
            issues.append(
                f"ERROR {prefix}: invalid accessibility role '{a11y.role}'")
    else:
        issues.append(
            f"WARN {prefix}: no explicit role; "
            f"will infer '{effective.value}' from '{component_type}'")

    # --- label requirement ---
    if effective in _ROLES_REQUIRING_NAME and not a11y.label:
        issues.append(
            f"ERROR {prefix}: role '{effective.value}' requires an "
            f"accessible name (label)")

    # --- focusable checks ---
    if a11y.effective_focusable(component_type) and not a11y.label:
        issues.append(
            f"WARN {prefix}: focusable component should have a label "
            f"for screen-reader discoverability")

    if a11y.tab_index is not None and not a11y.effective_focusable(component_type):
        issues.append(
            f"WARN {prefix}: tabindex={a11y.tab_index} set on a "
            f"non-focusable component")

    # --- live region ---
    if a11y.live_region is not None:
        if a11y.live_region not in ("polite", "assertive", "off"):
            issues.append(
                f"ERROR {prefix}: invalid live_region "
                f"'{a11y.live_region}' (must be polite/assertive/off)")

    return issues


def audit_a11y_tree(
    components: List[Dict[str, Any]],
) -> List[str]:
    """Audit an entire component tree for accessibility issues.

    Walks the tree depth-first and checks each component.

    Returns all issues found (empty = clean).
    """
    issues: List[str] = []

    def walk(comp: Dict[str, Any]) -> None:
        a11y_raw = comp.get("accessibility")
        if a11y_raw:
            a11y = A11yMetadata.from_dict(a11y_raw)
        else:
            a11y = A11yMetadata()
        issues.extend(validate_a11y(a11y, comp.get("type", ""), comp.get("id", "")))
        for child in comp.get("children", []):
            walk(child)

    for comp in components:
        walk(comp)

    return issues


# ---------------------------------------------------------------------------
# Document-level helpers (used by integration tests)
# ---------------------------------------------------------------------------


class ComponentA11y:
    """Lightweight wrapper that extracts A11yMetadata from a
    NstudioComponent (or raw dict) within a document context.

    The test suite expects ``ComponentA11y.from_component(comp, doc)``
    to return an object with a ``role`` property.
    """

    def __init__(self, a11y: A11yMetadata, component_type: str) -> None:
        self._a11y = a11y
        self._type = component_type
        self.role: str = a11y.effective_role(component_type).value

    @classmethod
    def from_component(cls, comp: Any, doc: Any = None) -> "ComponentA11y":
        """Build from a NstudioComponent (with .accessibility dict)
        or a raw dict.
        """
        a11y_raw = getattr(comp, "accessibility", None)
        if a11y_raw is None and hasattr(comp, "properties"):
            # NstudioComponent stores it inside properties
            a11y_raw = (comp.properties or {}).get("accessibility")
        if a11y_raw is None and hasattr(comp, "to_dict"):
            d = comp.to_dict() if callable(comp.to_dict) else {}
            a11y_raw = d.get("accessibility")
        if a11y_raw is None and isinstance(comp, dict):
            a11y_raw = comp.get("accessibility")

        a11y = A11yMetadata.from_dict(a11y_raw) if a11y_raw else A11yMetadata()
        comp_type = getattr(comp, "type", None) or (
            comp.get("type", "") if isinstance(comp, dict) else "")
        return cls(a11y, comp_type)


def audit_document(doc: Any) -> List[Dict[str, Any]]:
    """Audit all components in a NstudioDocument for accessibility issues.

    Returns a list of issue dicts with ``severity`` and ``message`` keys.
    """
    all_components: List[Dict[str, Any]] = []

    def collect(comp: Any) -> None:
        # Support both NstudioComponent objects and raw dicts
        if isinstance(comp, dict):
            all_components.append(comp)
            for child in comp.get("children", []):
                collect(child)
        elif hasattr(comp, "to_dict"):
            d = comp.to_dict() if callable(comp.to_dict) else {}
            all_components.append(d)
            for child in getattr(comp, "children", []):
                collect(child)
        elif hasattr(comp, "children"):
            props = getattr(comp, "properties", {}) or {}
            d = {"id": getattr(comp, "id", ""),
                 "type": getattr(comp, "type", ""),
                 "properties": props}
            # Read accessibility from attribute first, then from properties
            a11y = getattr(comp, "accessibility", None)
            if a11y is None:
                a11y = props.get("accessibility")
            if a11y:
                if isinstance(a11y, dict):
                    d["accessibility"] = a11y
                elif hasattr(a11y, "to_dict"):
                    d["accessibility"] = a11y.to_dict()
            all_components.append(d)
            for child in comp.children:
                collect(child)

    # Collect from screens
    for screen in getattr(doc, "screens", []):
        root = getattr(screen, "root", None)
        if root is not None:
            collect(root)

    # Also collect top-level components if doc has them
    components_list = getattr(doc, "components", None)
    if components_list:
        for c in components_list:
            collect(c)

    # Run audit and convert string issues to severity dicts
    raw_issues = audit_a11y_tree(all_components)
    issues: List[Dict[str, Any]] = []
    for msg in raw_issues:
        severity = "error" if msg.startswith("ERROR") else \
                   "warning" if msg.startswith("WARNING") else "info"
        issues.append({"severity": severity, "message": msg})
    return issues
