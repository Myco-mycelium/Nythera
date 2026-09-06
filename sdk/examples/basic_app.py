#!/usr/bin/env python3
"""Basic Nyrqis SDK example application.

This example demonstrates:
1. Creating a new project
2. Using the telemetry system
3. Performance monitoring
4. System restore points

Usage:
    python basic_app.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Add SDK to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from nyrqis_sdk.telemetry import Telemetry
from nyrqis_sdk.performance import Timer, MemoryTracker, FrameRateMonitor
from nyrqis_sdk.restore import RestoreManager


def main():
    """Run the example application."""
    print("=== Nyrqis SDK Basic Example ===\n")
    
    # 1. Initialize telemetry (opt-in)
    print("1. Telemetry")
    telemetry = Telemetry()
    telemetry.enable()
    telemetry.metric("app_start", 1.0)
    print("   Telemetry enabled, metric recorded")
    
    # 2. Performance monitoring
    print("\n2. Performance Monitoring")
    
    # Timer
    with Timer("example_operation") as timer:
        time.sleep(0.01)  # Simulate work
    print(f"   Timer: {timer.result.duration_ms:.2f} ms")
    
    # Memory tracking
    tracker = MemoryTracker()
    tracker.snapshot("start")
    data = [0] * (1024 * 100)  # Allocate some memory
    tracker.snapshot("end")
    delta = tracker.delta("start", "end")
    print(f"   Memory delta: {delta / 1024:.1f} KB")
    
    # Frame rate monitoring
    monitor = FrameRateMonitor()
    for _ in range(10):
        monitor.tick()
        time.sleep(0.016)  # ~60fps
    print(f"   FPS: {monitor.fps:.1f}")
    
    # 3. Restore points
    print("\n3. System Restore Points")
    manager = RestoreManager()
    point = manager.create("Example restore point")
    print(f"   Created: {point.id} ({point.age_hours:.2f}h old)")
    print(f"   Total points: {manager.point_count}")
    
    # 4. Package manager
    print("\n4. Package Manager")
    try:
        from ui.package_manager import PackageManager
        pm = PackageManager()
        stats = pm.get_stats()
        print(f"   Packages: {stats['total_packages']}")
        print(f"   Installed: {stats['installed']}")
        print(f"   Updatable: {stats['updatable']}")
    except ImportError:
        print("   (Package manager not available in this context)")
    
    # 5. Cleanup
    telemetry.flush()
    print("\n=== Example Complete ===")


if __name__ == "__main__":
    main()
