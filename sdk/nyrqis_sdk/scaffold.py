"""Nyrqis SDK scaffolding tool.

Generates project templates for Nyrqis application development.

Usage:
    python -m nyrqis_sdk.scaffold my-app
    python -m nyrqis_sdk.scaffold my-app --template shell
    python -m nyrqis_sdk.scaffold my-app --template app

References:
    - NPC-003: Engineering Handbook
    - API-001: Public API specification
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

# Template definitions
TEMPLATES = {
    "app": {
        "description": "Standard Nyrqis application",
        "files": {
            "pyproject.toml": '''[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "{name}"
version = "0.1.0"
description = "{description}"
requires-python = ">=3.12"
dependencies = [
    "nyrqis-sdk>=0.1.0",
]

[project.scripts]
{name} = "{name}.main:main"
''',
            "{package_name}/__init__.py": '''"""Nyrqis application: {name}."""
__version__ = "0.1.0"
''',
            "{package_name}/main.py": '''"""Main entry point for {name}."""
from __future__ import annotations

import sys


def main():
    """Run the application."""
    print("Hello from {name}!")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
''',
            "tests/__init__.py": "",
            "tests/test_main.py": '''"""Tests for {name}."""
from {package_name}.main import main


def test_main():
    """Test main function."""
    result = main()
    assert result == 0
''',
            "README.md": '''# {name}

{description}

## Installation

```bash
pip install -e .
```

## Usage

```bash
python -m {name}
```

## Development

```bash
# Run tests
pytest tests/

# Build
python -m build
```
''',
            ".gitignore": '''__pycache__/
*.pyc
*.pyo
*.egg-info/
dist/
build/
.venv/
''',
        },
    },
    "shell": {
        "description": "Nyrqis shell design (.nstudio)",
        "files": {
            "design.nstudio": '''{{
  "version": "1.0.0",
  "screens": [
    {{
      "id": "main",
      "size": {{"width": 1440, "height": 900}}
    }}
  ],
  "components": [
    {{
      "type": "Screen",
      "id": "screen_main",
      "layout": {{"x": 0, "y": 0, "width": 1440, "height": 900}},
      "properties": {{"background": "$theme.background"}}
    }}
  ]
}}
''',
            "README.md": '''# {name} Shell Design

{description}

## Preview

```bash
python tools/preview_server.py --file design.nstudio
```

Then open http://localhost:8080 in your browser.
''',
            ".gitignore": '''__pycache__/
*.pyc
''',
        },
    },
    "rust": {
        "description": "Rust library for Nyrqis",
        "files": {
            "Cargo.toml": '''[package]
name = "{name}"
version = "0.1.0"
edition = "2021"
description = "{description}"

[lib]
name = "{name}"
crate-type = ["cdylib", "rlib"]

[dependencies]
libc = "0.2"

[dev-dependencies]
''',
            "src/lib.rs": """//! {name} — {description}
//!
//! This crate provides {description_lower}.

use std::os::raw::c_int;

/// ABI version: 0x0001_0000 (1.0.0).
const ABI_VERSION: u32 = 0x0001_0000;

/// Return the ABI version of this crate.
#[no_mangle]
pub extern "C" fn version() -> u32 {{
    ABI_VERSION
}}

#[cfg(test)]
mod tests {{
    use super::*;

    #[test]
    fn version_returns_abi_version() {{
        assert_eq!(version(), 0x0001_0000);
    }}
}}
""",
            "README.md": '''# {name}

{description}

## Build

```bash
cargo build --release
```

## Test

```bash
cargo test
```
''',
            ".gitignore": '''target/
Cargo.lock
''',
        },
    },
}


def generate_project(
    name: str,
    template: str = "app",
    description: str = "",
    output_dir: Optional[str] = None,
) -> Path:
    """Generate a new Nyrqis project from a template.
    
    Parameters
    ----------
    name : str
        Project name (used for directory and package name).
    template : str
        Template type: "app", "shell", or "rust".
    description : str
        Project description.
    output_dir : str, optional
        Output directory. Defaults to current directory.
        
    Returns
    -------
    Path
        Path to the created project directory.
    """
    if template not in TEMPLATES:
        raise ValueError(f"Unknown template: {template}. Available: {list(TEMPLATES.keys())}")
    
    tmpl = TEMPLATES[template]
    
    # Create project directory
    base_dir = Path(output_dir) if output_dir else Path.cwd()
    # Normalize package name (replace hyphens with underscores for Python)
    package_name = name.replace("-", "_")
    project_dir = base_dir / name
    
    if project_dir.exists():
        raise FileExistsError(f"Directory already exists: {project_dir}")
    
    project_dir.mkdir(parents=True)
    
    # Generate files
    for file_path, content in tmpl["files"].items():
        # Format file path with project name
        formatted_path = file_path.format(name=name, package_name=package_name)
        
        # Format content with project name and description
        description_lower = description.lower() if description else name.lower()
        formatted_content = content.format(
            name=name,
            package_name=package_name,
            description=description or f"Nyrqis {template} project",
            description_lower=description_lower,
        )
        
        # Create file
        full_path = project_dir / formatted_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(formatted_content)
    
    return project_dir


def main():
    """CLI entry point for scaffolding."""
    parser = argparse.ArgumentParser(
        description="Nyrqis SDK — Project scaffolding tool"
    )
    parser.add_argument(
        "name",
        help="Project name"
    )
    parser.add_argument(
        "-t", "--template",
        choices=list(TEMPLATES.keys()),
        default="app",
        help="Template type (default: app)"
    )
    parser.add_argument(
        "-d", "--description",
        default="",
        help="Project description"
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Output directory (default: current directory)"
    )
    parser.add_argument(
        "--list-templates",
        action="store_true",
        help="List available templates"
    )
    
    args = parser.parse_args()
    
    if args.list_templates:
        print("Available templates:")
        for name, tmpl in TEMPLATES.items():
            print(f"  {name:10s} — {tmpl['description']}")
        return
    
    try:
        project_dir = generate_project(
            name=args.name,
            template=args.template,
            description=args.description,
            output_dir=args.output,
        )
        print(f"Created project: {project_dir}")
        print(f"Template: {args.template} — {TEMPLATES[args.template]['description']}")
        print(f"\nNext steps:")
        print(f"  cd {project_dir}")
        if args.template == "rust":
            print(f"  cargo build --release")
        else:
            print(f"  pip install -e .")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
