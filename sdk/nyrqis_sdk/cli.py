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


def cmd_pkg(args):
    """Package management commands."""
    try:
        # Add backend to path
        backend_dir = Path(__file__).parent.parent.parent / "source" / "nyhal-linux-backend"
        if backend_dir.exists():
            sys.path.insert(0, str(backend_dir))
        
        from ui.package_manager import PackageManager
        pm = PackageManager()
        
        if args.pkg_command == "list":
            pkgs = pm.get_installed() if args.installed else pm.get_available()
            if args.updatable:
                pkgs = pm.get_updatable()
            
            print(f"{'Package':<25} {'Version':<15} {'Status':<10}")
            print("-" * 50)
            for p in pkgs[:args.limit]:
                print(f"{p.name:<25} {p.version:<15} {p.status.value:<10}")
            print(f"\n{len(pkgs)} packages shown")
            return 0
        
        elif args.pkg_command == "search":
            results = pm.search(args.query)
            print(f"Found {len(results)} packages matching '{args.query}':")
            for p in results[:args.limit]:
                print(f"  {p.status_icon} {p.name} {p.version} — {p.description[:50]}")
            return 0
        
        elif args.pkg_command == "info":
            pkg = pm.select_package(args.name)
            if not pkg:
                print(f"Package '{args.name}' not found", file=sys.stderr)
                return 1
            
            print(f"Name: {pkg.name}")
            print(f"Version: {pkg.version}")
            print(f"Latest: {pkg.latest_version}")
            print(f"Status: {pkg.status.value}")
            print(f"Description: {pkg.description}")
            print(f"Size: {pkg.size_display}")
            print(f"License: {pkg.license}")
            print(f"Dependencies: {', '.join(pkg.dependencies) or 'None'}")
            return 0
        
        elif args.pkg_command == "install":
            op = pm.install_package(args.name)
            if op:
                print(f"✅ Installed {args.name}")
                return 0
            else:
                print(f"❌ Failed to install {args.name}", file=sys.stderr)
                return 1
        
        elif args.pkg_command == "remove":
            op = pm.remove_package(args.name)
            if op:
                print(f"✅ Removed {args.name}")
                return 0
            else:
                print(f"❌ Failed to remove {args.name}", file=sys.stderr)
                return 1
        
        elif args.pkg_command == "update":
            if args.name:
                op = pm.update_package(args.name)
                if op:
                    print(f"✅ Updated {args.name}")
                    return 0
                else:
                    print(f"❌ Failed to update {args.name}", file=sys.stderr)
                    return 1
            else:
                count = pm.upgrade_all()
                print(f"✅ Updated {count} packages")
                return 0
        
        elif args.pkg_command == "stats":
            stats = pm.get_stats()
            print(f"Total packages: {stats['total_packages']}")
            print(f"Installed: {stats['installed']}")
            print(f"Updatable: {stats['updatable']}")
            print(f"Available: {stats['available']}")
            return 0
        
    except ImportError as e:
        print(f"❌ Package manager not available: {e}", file=sys.stderr)
        return 1
    
    return 0


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
    
    # pkg command group
    pkg_parser = subparsers.add_parser("pkg", help="Package management")
    pkg_subparsers = pkg_parser.add_subparsers(dest="pkg_command", help="Package operations")
    
    # pkg list
    list_pkg_parser = pkg_subparsers.add_parser("list", help="List packages")
    list_pkg_parser.add_argument("--installed", action="store_true", help="Show installed packages")
    list_pkg_parser.add_argument("--updatable", action="store_true", help="Show updatable packages")
    list_pkg_parser.add_argument("-n", "--limit", type=int, default=20, help="Max packages to show")
    
    # pkg search
    search_parser = pkg_subparsers.add_parser("search", help="Search packages")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("-n", "--limit", type=int, default=10, help="Max results")
    
    # pkg info
    info_parser = pkg_subparsers.add_parser("info", help="Show package info")
    info_parser.add_argument("name", help="Package name")
    
    # pkg install
    install_parser = pkg_subparsers.add_parser("install", help="Install a package")
    install_parser.add_argument("name", help="Package name")
    
    # pkg remove
    remove_parser = pkg_subparsers.add_parser("remove", help="Remove a package")
    remove_parser.add_argument("name", help="Package name")
    
    # pkg update
    update_parser = pkg_subparsers.add_parser("update", help="Update packages")
    update_parser.add_argument("name", nargs="?", help="Package name (omit for all)")
    
    # pkg stats
    pkg_subparsers.add_parser("stats", help="Show package statistics")
    
    pkg_parser.set_defaults(func=cmd_pkg)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 0
    
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
