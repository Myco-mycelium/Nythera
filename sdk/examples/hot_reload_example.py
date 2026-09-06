#!/usr/bin/env python3
"""Nyrqis SDK hot reload example.

This example demonstrates:
1. Watching a single .nstudio file for changes
2. Watching a directory for .nstudio changes
3. Triggering actions on file changes

Usage:
    1. Create a test.nstudio file in the current directory
    2. Run this script
    3. Edit test.nstudio and watch the output
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Add SDK to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from nyrqis_sdk.hotreload import HotReloader, DirectoryReloader


def on_file_changed(path: Path):
    """Called when a file changes."""
    print(f"  File changed: {path.name}")


def on_directory_change(path: Path):
    """Called when any .nstudio file in the directory changes."""
    print(f"  Directory change: {path.name}")


def main():
    """Run the hot reload example."""
    print("=== Nyrqis SDK Hot Reload Example ===\n")
    
    # Create a test file if it doesn't exist
    test_file = Path("test.nstudio")
    if not test_file.exists():
        test_file.write_text('{"version": "1.0.0", "screens": []}')
        print(f"Created {test_file}")
    
    # 1. Single file watcher
    print("\n1. Single File Watcher")
    reloader = HotReloader(test_file, interval=0.5)
    reloader.on_change(on_file_changed)
    reloader.start()
    print(f"   Watching: {test_file}")
    print("   Edit the file and watch for changes...")
    
    # Wait a bit then stop
    time.sleep(2)
    reloader.stop()
    
    # 2. Directory watcher
    print("\n2. Directory Watcher")
    current_dir = Path(".")
    dir_reloader = DirectoryReloader(current_dir, interval=0.5)
    dir_reloader.on_change(on_directory_change)
    dir_reloader.start()
    print(f"   Watching: {current_dir}")
    print("   Create/edit .nstudio files and watch for changes...")
    
    # Wait a bit then stop
    time.sleep(2)
    dir_reloader.stop()
    
    # Cleanup
    if test_file.exists():
        test_file.unlink()
        print(f"\nCleaned up {test_file}")
    
    print("\n=== Example Complete ===")


if __name__ == "__main__":
    main()
