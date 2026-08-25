"""NUI Localization System (NUI-SCHEMA §8.1).

Manages locale files, resolves ``$localize:key`` references, and
validates that all localization keys in a document exist in the
active locale.

Architecture:

  locales/
  ├── en/
  │   ├── ui.json          # UI strings
  │   └── errors.json      # Error messages
  ├── af-ZA/
  │   ├── ui.json
  │   └── errors.json
  └── ...

Each locale directory contains JSON files whose keys are flat dotted
paths (``settings.save``) mapped to translated strings.

The ``$localize:key`` reference in NUI properties resolves through the
active locale at design time (Nyforge), at import time (nstudio.py),
and at runtime (Nyrqis runtime).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Regex for $localize: references (same as nstudio.py)
# ---------------------------------------------------------------------------

_LOCALIZE_RE = re.compile(r"\$localize:([A-Za-z0-9_.\-]+)")


# ---------------------------------------------------------------------------
# Locale table
# ---------------------------------------------------------------------------

@dataclass
class LocaleTable:
    """One locale's string table (e.g. ``en/ui.json``)."""

    locale: str
    strings: Dict[str, str] = field(default_factory=dict)

    def resolve(self, key: str) -> Optional[str]:
        """Resolve a dotted key like ``settings.save``."""
        return self.strings.get(key)

    def has_key(self, key: str) -> bool:
        return key in self.strings

    def keys(self) -> List[str]:
        return sorted(self.strings.keys())


# ---------------------------------------------------------------------------
# Locale Manager
# ---------------------------------------------------------------------------

class LocaleManager:
    """Loads and manages locale files for a NUI project.

    Usage::

        lm = LocaleManager()
        lm.load_directory("locales/")           # auto-discovers en/, af-ZA/, ...
        lm.set_active("en")

        text = lm.resolve_string("Save")        # "Save" (en)
        text = lm.resolve_string("$localize:settings.save")

        issues = lm.validate_document(doc)      # check all $localize: refs
    """

    def __init__(self) -> None:
        self._locales: Dict[str, LocaleTable] = {}
        self._active: str = "en"

    @property
    def active_locale(self) -> str:
        return self._active

    @property
    def available_locales(self) -> List[str]:
        return sorted(self._locales.keys())

    # ---- loading ----

    def load_directory(self, path: str) -> int:
        """Load all locale directories under *path*.

        Expected structure::

            locales/
            ├── en/
            │   └── *.json
            ├── af-ZA/
            │   └── *.json

        Returns the number of locale files loaded.
        """
        count = 0
        if not os.path.isdir(path):
            return 0

        for entry in sorted(os.listdir(path)):
            locale_dir = os.path.join(path, entry)
            if not os.path.isdir(locale_dir):
                continue

            table = self._locales.setdefault(entry, LocaleTable(locale=entry))

            for fname in sorted(os.listdir(locale_dir)):
                if not fname.endswith(".json"):
                    continue
                fpath = os.path.join(locale_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        self._flatten(table.strings, data)
                        count += 1
                except (json.JSONDecodeError, OSError):
                    pass

        return count

    def load_dict(self, locale: str, data: Dict[str, Any]) -> None:
        """Load a flat or nested dict directly as a locale's strings."""
        table = self._locales.setdefault(locale, LocaleTable(locale=locale))
        self._flatten(table.strings, data)

    def load_inline(self, locales_dict: Dict[str, Any]) -> None:
        """Load the NUI ``locales`` section format:
        ``{"active": "en", "tables": {"en": {"key": "val"}}}``
        """
        if "active" in locales_dict:
            self._active = locales_dict["active"]
        tables = locales_dict.get("tables", {})
        for locale, table_data in tables.items():
            if isinstance(table_data, dict):
                self.load_dict(locale, table_data)

    def set_active(self, locale: str) -> None:
        self._active = locale

    # ---- resolution ----

    def resolve_string(self, text: str) -> str:
        """Resolve ``$localize:key`` references in *text*.

        If *text* is not a string or has no ``$localize:`` prefix, it is
        returned unchanged.  Missing keys are left as-is with the
        ``$localize:`` prefix preserved.
        """
        if not isinstance(text, str) or "$localize:" not in text:
            return text

        table = self._locales.get(self._active)
        if table is None:
            return text

        def _replace(m: re.Match) -> str:
            key = m.group(1)
            value = table.resolve(key)
            return value if value is not None else m.group(0)

        return _LOCALIZE_RE.sub(_replace, text)

    def resolve_recursive(self, obj: Any) -> Any:
        """Deep-resolve all ``$localize:`` strings in an arbitrary structure."""
        if isinstance(obj, str):
            return self.resolve_string(obj)
        if isinstance(obj, dict):
            return {k: self.resolve_recursive(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.resolve_recursive(item) for item in obj]
        return obj

    # ---- validation ----

    def validate_document(self, doc: Any) -> List[str]:
        """Validate all ``$localize:`` references in a NUI document.

        Returns a list of warning strings.  Empty = all keys present.
        """
        issues: List[str] = []
        table = self._locales.get(self._active)

        def walk(obj: Any, path: str = "") -> None:
            if isinstance(obj, str):
                for m in _LOCALIZE_RE.finditer(obj):
                    key = m.group(1)
                    if table is None:
                        issues.append(
                            f"WARN {path}: $localize:{key} — no locales "
                            f"loaded (active='{self._active}')")
                    elif not table.has_key(key):
                        issues.append(
                            f"WARN {path}: $localize:{key} not found in "
                            f"locale '{self._active}'")
                return
            if isinstance(obj, dict):
                for k, v in obj.items():
                    walk(v, f"{path}.{k}" if path else k)
                return
            if isinstance(obj, list):
                for i, item in enumerate(obj):
                    walk(item, f"{path}[{i}]")

        # Walk top-level document fields
        walk(getattr(doc, "states", {}), "states")
        for screen in getattr(doc, "screens", []):
            walk(screen.root, f"screen:{screen.id}")
        for b in getattr(doc, "behaviors", []):
            walk(b.condition, f"behavior:{b.id}.condition")
            walk(b.action, f"behavior:{b.id}.action")

        return issues

    def check_key_exists(self, key: str) -> bool:
        """Check if a key exists in the active locale."""
        table = self._locales.get(self._active)
        if table is None:
            return False
        return table.has_key(key)

    def missing_keys(self) -> List[str]:
        """Return keys referenced in the active locale but missing."""
        table = self._locales.get(self._active)
        if table is None:
            return []
        return [k for k in table.keys()
                if not table.has_key(k)]

    # ---- helpers ----

    @staticmethod
    def _flatten(out: Dict[str, str], d: Dict[str, Any], prefix: str = "") -> None:
        """Flatten a nested dict with dotted keys: ``{"a": {"b": "c"}}`` →
        ``{"a.b": "c"}``.
        """
        for k, v in d.items():
            full_key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                LocaleManager._flatten(out, v, full_key)
            else:
                out[full_key] = str(v)

    def summary(self) -> Dict[str, Any]:
        """Return a summary dict for debugging/logging."""
        return {
            "active": self._active,
            "locales": {name: len(table.strings)
                        for name, table in self._locales.items()},
            "total_keys": sum(len(t.strings) for t in self._locales.values()),
        }
