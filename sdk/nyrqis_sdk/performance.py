"""Nyrqis SDK performance monitoring utilities.

Provides lightweight performance measurement tools for Nyrqis applications:
- Timing decorators and context managers
- Memory usage tracking
- Frame rate monitoring
- System health checks

Usage:
    from nyrqis_sdk.performance import Timer, MemoryTracker, FrameRateMonitor
    
    # Measure execution time
    with Timer("my_operation"):
        do_something()
    
    # Track memory usage
    tracker = MemoryTracker()
    tracker.snapshot("before")
    allocate_large_object()
    tracker.snapshot("after")
    print(tracker.delta("before", "after"))
    
    # Monitor frame rate
    monitor = FrameRateMonitor()
    while running:
        render_frame()
        monitor.tick()
    print(f"Average FPS: {monitor.fps}")

References:
    - BENCHMARK_PLAN.md: Performance measurement methodology
    - NPC-003 §6: Testing standards
"""

from __future__ import annotations

import functools
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class TimingResult:
    """Result of a timing measurement."""
    name: str
    duration_ms: float
    timestamp: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def duration_us(self) -> float:
        """Duration in microseconds."""
        return self.duration_ms * 1000
    
    @property
    def duration_s(self) -> float:
        """Duration in seconds."""
        return self.duration_ms / 1000


class Timer:
    """Context manager and decorator for timing code blocks.
    
    Usage as context manager:
        with Timer("my_operation"):
            do_something()
    
    Usage as decorator:
        @Timer("my_function")
        def my_function():
            do_something()
    
    Parameters
    ----------
    name : str
        Name of the timing measurement.
    callback : callable, optional
        Called with TimingResult when measurement completes.
    """
    
    def __init__(
        self,
        name: str,
        callback: Optional[Callable[[TimingResult], None]] = None,
    ) -> None:
        self._name = name
        self._callback = callback
        self._start: Optional[float] = None
        self._result: Optional[TimingResult] = None
    
    @property
    def result(self) -> Optional[TimingResult]:
        """The timing result (available after context exits)."""
        return self._result
    
    def __enter__(self) -> Timer:
        self._start = time.perf_counter()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._start is not None:
            duration_ms = (time.perf_counter() - self._start) * 1000
            self._result = TimingResult(
                name=self._name,
                duration_ms=duration_ms,
                timestamp=time.time(),
            )
            if self._callback:
                self._callback(self._result)
    
    def __call__(self, func: Callable) -> Callable:
        """Use as a decorator."""
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with self:
                return func(*args, **kwargs)
        return wrapper


class MemoryTracker:
    """Track memory usage across snapshots.
    
    Usage:
        tracker = MemoryTracker()
        tracker.snapshot("start")
        # ... do work ...
        tracker.snapshot("end")
        delta = tracker.delta("start", "end")
        print(f"Memory change: {delta / 1024 / 1024:.1f} MB")
    """
    
    def __init__(self) -> None:
        self._snapshots: Dict[str, int] = {}
        self._lock = threading.Lock()
    
    def snapshot(self, name: str) -> int:
        """Take a memory snapshot.
        
        Parameters
        ----------
        name : str
            Name for this snapshot.
            
        Returns
        -------
        int
            Current RSS in bytes.
        """
        rss = self._get_rss()
        with self._lock:
            self._snapshots[name] = rss
        return rss
    
    def get(self, name: str) -> Optional[int]:
        """Get a snapshot by name."""
        with self._lock:
            return self._snapshots.get(name)
    
    def delta(self, start: str, end: str) -> Optional[int]:
        """Calculate memory delta between two snapshots.
        
        Returns bytes (positive = growth, negative = shrinkage).
        """
        with self._lock:
            start_rss = self._snapshots.get(start)
            end_rss = self._snapshots.get(end)
        
        if start_rss is None or end_rss is None:
            return None
        
        return end_rss - start_rss
    
    def summary(self) -> Dict[str, Any]:
        """Get a summary of all snapshots."""
        with self._lock:
            snapshots = dict(self._snapshots)
        
        if not snapshots:
            return {"snapshots": 0}
        
        values = list(snapshots.values())
        return {
            "snapshots": len(snapshots),
            "min_mb": min(values) / 1024 / 1024,
            "max_mb": max(values) / 1024 / 1024,
            "current_mb": values[-1] / 1024 / 1024,
        }
    
    def _get_rss(self) -> int:
        """Get current RSS (Resident Set Size) in bytes."""
        # Try Linux /proc/self/status
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        # VmRSS:    12345 kB
                        parts = line.split()
                        return int(parts[1]) * 1024
        except (OSError, ValueError):
            pass
        
        # Try resource module
        try:
            import resource
            usage = resource.getrusage(resource.RUSAGE_SELF)
            # ru_maxrss is in KB on Linux, bytes on macOS
            if hasattr(os, 'uname') and os.uname().sysname == "Darwin":
                return usage.ru_maxrss
            return usage.ru_maxrss * 1024
        except (ImportError, AttributeError):
            pass
        
        return 0


class FrameRateMonitor:
    """Monitor frame rate for real-time applications.
    
    Usage:
        monitor = FrameRateMonitor()
        while running:
            render_frame()
            monitor.tick()
        
        print(f"Average FPS: {monitor.fps}")
        print(f"Frame time: {monitor.frame_time_ms} ms")
    """
    
    def __init__(self, window_size: int = 60) -> None:
        self._window_size = window_size
        self._frame_times: List[float] = []
        self._last_tick: Optional[float] = None
        self._lock = threading.Lock()
    
    def tick(self) -> None:
        """Record a frame tick."""
        now = time.perf_counter()
        
        with self._lock:
            if self._last_tick is not None:
                dt = now - self._last_tick
                self._frame_times.append(dt)
                
                # Keep only recent frames
                if len(self._frame_times) > self._window_size:
                    self._frame_times.pop(0)
            
            self._last_tick = now
    
    @property
    def fps(self) -> float:
        """Current frames per second."""
        with self._lock:
            if len(self._frame_times) < 2:
                return 0.0
            
            avg_frame_time = sum(self._frame_times) / len(self._frame_times)
            return 1.0 / avg_frame_time if avg_frame_time > 0 else 0.0
    
    @property
    def frame_time_ms(self) -> float:
        """Average frame time in milliseconds."""
        with self._lock:
            if not self._frame_times:
                return 0.0
            
            avg_frame_time = sum(self._frame_times) / len(self._frame_times)
            return avg_frame_time * 1000
    
    @property
    def min_fps(self) -> float:
        """Minimum FPS in the window."""
        with self._lock:
            if len(self._frame_times) < 2:
                return 0.0
            
            max_frame_time = max(self._frame_times)
            return 1.0 / max_frame_time if max_frame_time > 0 else 0.0
    
    @property
    def max_fps(self) -> float:
        """Maximum FPS in the window."""
        with self._lock:
            if len(self._frame_times) < 2:
                return 0.0
            
            min_frame_time = min(self._frame_times)
            return 1.0 / min_frame_time if min_frame_time > 0 else 0.0
    
    def reset(self) -> None:
        """Reset all frame time data."""
        with self._lock:
            self._frame_times.clear()
            self._last_tick = None
    
    def summary(self) -> Dict[str, float]:
        """Get a summary of frame rate statistics."""
        return {
            "fps": self.fps,
            "frame_time_ms": self.frame_time_ms,
            "min_fps": self.min_fps,
            "max_fps": self.max_fps,
            "samples": len(self._frame_times),
        }


class PerformanceBudget:
    """Track performance budgets and warn on violations.
    
    Usage:
        budget = PerformanceBudget()
        budget.set("startup_time_ms", max_value=1000)
        budget.set("memory_mb", max_value=512)
        
        # Check budget
        budget.check("startup_time_ms", 500)  # OK
        budget.check("startup_time_ms", 1500)  # Warning!
    """
    
    def __init__(self) -> None:
        self._budgets: Dict[str, float] = {}
        self._violations: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
    
    def set(self, name: str, max_value: float) -> None:
        """Set a performance budget."""
        with self._lock:
            self._budgets[name] = max_value
    
    def check(self, name: str, value: float) -> bool:
        """Check if a value is within budget.
        
        Returns True if within budget, False if violated.
        """
        with self._lock:
            max_value = self._budgets.get(name)
            
            if max_value is None:
                return True  # No budget defined
            
            if value > max_value:
                self._violations.append({
                    "name": name,
                    "value": value,
                    "max_value": max_value,
                    "timestamp": time.time(),
                })
                return False
            
            return True
    
    @property
    def violation_count(self) -> int:
        """Number of budget violations."""
        with self._lock:
            return len(self._violations)
    
    def get_violations(self) -> List[Dict[str, Any]]:
        """Get all violations."""
        with self._lock:
            return list(self._violations)
    
    def summary(self) -> Dict[str, Any]:
        """Get a summary of budgets and violations."""
        with self._lock:
            return {
                "budgets": dict(self._budgets),
                "violations": len(self._violations),
            }


__all__ = [
    "Timer",
    "MemoryTracker",
    "FrameRateMonitor",
    "PerformanceBudget",
    "TimingResult",
]
