"""Nyrqis SDK CLI — Developer tools for Nyrqis application development.

Usage:
    nyq new my-app              # Create new app (default template)
    nyq new my-app -t shell     # Create new shell design
    nyq new my-app -t rust      # Create new Rust library
    nyq list-templates          # List available templates
    nyq build                   # Build the current project
    nyq test                    # Run tests
    nyq preview                 # Start preview server

References:
    - NPC-003: Engineering Handbook
    - BUILD-001: Build Architecture
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def cmd_new(args):
    """Create a new Nyrqis project."""
    from .scaffold import generate_project, TEMPLATES
    
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
            output=args.output,
        )
        print(f"✅ Created project: {project_dir}")
        print(f"   Template: {args.template}")
        print(f"\nNext steps:")
        print(f"  cd {project_dir}")
        if args.template == "rust":
            print(f"  cargo build --release")
        else:
            print(f"  pip install -e .")
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_build(args):
    """Build the current project."""
    project_dir = Path.cwd()
    
    # Detect project type
    if (project_dir / "Cargo.toml").exists():
        print("Building Rust project...")
        result = subprocess.run(
            ["cargo", "build", "--release"] if not args.debug else ["cargo", "build"],
            cwd=project_dir,
        )
        return result.returncode
    elif (project_dir / "pyproject.toml").exists():
        print("Building Python package...")
        result = subprocess.run(
            [sys.executable, "-m", "build"],
            cwd=project_dir,
        )
        return result.returncode
    else:
        print("❌ No Cargo.toml or pyproject.toml found", file=sys.stderr)
        return 1


def cmd_test(args):
    """Run tests."""
    project_dir = Path.cwd()
    
    # Detect project type
    if (project_dir / "Cargo.toml").exists():
        print("Running Rust tests...")
        result = subprocess.run(
            ["cargo", "test"],
            cwd=project_dir,
        )
        return result.returncode
    elif (project_dir / "pyproject.toml").exists():
        print("Running Python tests...")
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/"],
            cwd=project_dir,
        )
        return result.returncode
    else:
        print("❌ No Cargo.toml or pyproject.toml found", file=sys.stderr)
        return 1


def cmd_preview(args):
    """Start preview server for shell designs."""
    # Find .nstudio files
    nstudio_files = list(Path.cwd().glob("*.nstudio"))
    if not nstudio_files:
        print("❌ No .nstudio files found in current directory", file=sys.stderr)
        return 1
    
    # Use the first .nstudio file
    nstudio_file = nstudio_files[0]
    print(f"Starting preview server for {nstudio_file}...")
    
    # Find the preview server script
    backend_dir = Path(__file__).parent.parent.parent / "source" / "nyhal-linux-backend"
    preview_script = backend_dir.parent / "tools" / "preview_server.py"
    
    if not preview_script.exists():
        # Try alternative location
        preview_script = Path(__file__).parent.parent.parent / "tools" / "preview_server.py"
    
    if not preview_script.exists():
        print("❌ Preview server not found", file=sys.stderr)
        return 1
    
    result = subprocess.run(
        [sys.executable, str(preview_script), "--file", str(nstudio_file)],
        cwd=project_dir,
    )
    return result.returncode


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="nyq",
        description="Nyrqis SDK — Developer tools for Nyrqis application development"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # new command
    new_parser = subparsers.add_parser("new", help="Create a new project")
    new_parser.add_argument("name", help="Project name")
    new_parser.add_argument("-t", "--template", choices=["app", "shell", "rust"], default="app",
                           help="Template type (default: app)")
    new_parser.add_argument("-d", "--description", default="", help="Project description")
    new_parser.add_argument("-o", "--output", default=None, help="Output directory")
    new_parser.add_argument("--list-templates", action="store_true", help="List available templates")
    new_parser.set_defaults(func=cmd_new)
    
    # build command
    build_parser = subparsers.add_parser("build", help="Build the current project")
    build_parser.add_argument("--debug", action="store_true", help="Build in debug mode")
    build_parser.set_defaults(func=cmd_build)
    
    # test command
    test_parser = subparsers.add_parser("test", help="Run tests")
    test_parser.set_defaults(func=cmd_test)
    
    # preview command
    preview_parser = subparsers.add_parser("preview", help="Start preview server")
    preview_parser.set_defaults(func=cmd_preview)
    
    # list-templates command
    list_parser = subparsers.add_parser("list-templates", help="List available templates")
    list_parser.set_defaults(func=lambda args: cmd_new(argparse.Namespace(
        name="", template="app", description="", output=None, list_templates=True
    )))
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 0
    
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
