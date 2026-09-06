"""Nyrqis SDK telemetry and crash reporting.

Provides opt-in crash reporting, performance metrics collection,
and system health monitoring for Nyrqis applications.

All telemetry is strictly opt-in and privacy-respecting:
- No data is sent without explicit user consent
- No personally identifiable information is collected
- Users can view and delete all collected data
- Data is stored locally by default

Usage:
    from nyrqis_sdk.telemetry import Telemetry
    
    telemetry = Telemetry()
    telemetry.enable()  # Requires user consent
    
    # Report metrics
    telemetry.metric("startup_time_ms", 1234)
    telemetry.metric("memory_usage_mb", 512)
    
    # Report errors (with context, no PII)
    telemetry.error("render_failure", context={"screen": "desktop"})

References:
    - NPC-001 §6.5: Privacy requirements
    - NPC-010: Governance expansion
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class TelemetryStatus(Enum):
    """Telemetry consent status."""
    DISABLED = "disabled"
    ENABLED = "enabled"
    ERROR = "error"


@dataclass
class CrashReport:
    """A crash report with context and stack trace."""
    timestamp: float
    error_type: str
    message: str
    stack_trace: str
    context: Dict[str, Any] = field(default_factory=dict)
    system_info: Dict[str, str] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "timestamp": self.timestamp,
            "error_type": self.error_type,
            "message": self.message,
            "stack_trace": self.stack_trace,
            "context": self.context,
            "system_info": self.system_info,
        }
    
    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=2)


@dataclass
class MetricPoint:
    """A single metric measurement."""
    name: str
    value: float
    timestamp: float
    tags: Dict[str, str] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "value": self.value,
            "timestamp": self.timestamp,
            "tags": self.tags,
        }


class Telemetry:
    """Telemetry collector for Nyrqis applications.
    
    Parameters
    ----------
    data_dir : str or Path, optional
        Directory to store telemetry data. Defaults to ~/.nyrqis/telemetry
    auto_flush : bool
        Whether to automatically flush metrics to disk (default: True)
    flush_interval : float
        Interval in seconds for auto-flush (default: 60)
    """
    
    def __init__(
        self,
        data_dir: Optional[str | Path] = None,
        auto_flush: bool = True,
        flush_interval: float = 60.0,
    ) -> None:
        self._data_dir = Path(data_dir) if data_dir else Path.home() / ".nyrqis" / "telemetry"
        self._auto_flush = auto_flush
        self._flush_interval = flush_interval
        
        self._status = TelemetryStatus.DISABLED
        self._crash_reports: List[CrashReport] = []
        self._metrics: List[MetricPoint] = []
        self._lock = threading.Lock()
        self._flush_thread: Optional[threading.Thread] = None
        self._running = False
        
        # System info (collected once)
        self._system_info = self._collect_system_info()
    
    @property
    def status(self) -> TelemetryStatus:
        """Current telemetry status."""
        return self._status
    
    @property
    def is_enabled(self) -> bool:
        """Whether telemetry is enabled."""
        return self._status == TelemetryStatus.ENABLED
    
    @property
    def crash_report_count(self) -> int:
        """Number of pending crash reports."""
        with self._lock:
            return len(self._crash_reports)
    
    @property
    def metric_count(self) -> int:
        """Number of pending metrics."""
        with self._lock:
            return len(self._metrics)
    
    def enable(self) -> bool:
        """Enable telemetry collection.
        
        Returns True on success, False if already enabled.
        """
        if self._status == TelemetryStatus.ENABLED:
            return True
        
        # Create data directory
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._status = TelemetryStatus.ERROR
            return False
        
        self._status = TelemetryStatus.ENABLED
        
        # Start auto-flush thread
        if self._auto_flush:
            self._start_flush_thread()
        
        return True
    
    def disable(self) -> None:
        """Disable telemetry collection."""
        self._status = TelemetryStatus.DISABLED
        self._stop_flush_thread()
        self.flush()
    
    def metric(
        self,
        name: str,
        value: float,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        """Record a metric measurement.
        
        Parameters
        ----------
        name : str
            Metric name (e.g., "startup_time_ms", "memory_usage_mb").
        value : float
            Metric value.
        tags : dict, optional
            Additional tags for the metric.
        """
        if not self.is_enabled:
            return
        
        point = MetricPoint(
            name=name,
            value=value,
            timestamp=time.time(),
            tags=tags or {},
        )
        
        with self._lock:
            self._metrics.append(point)
    
    def error(
        self,
        error_type: str,
        message: str = "",
        context: Optional[Dict[str, Any]] = None,
        exc_info: Optional[tuple] = None,
    ) -> None:
        """Report an error.
        
        Parameters
        ----------
        error_type : str
            Error category (e.g., "render_failure", "ipc_timeout").
        message : str
            Human-readable error message.
        context : dict, optional
            Additional context (no PII!).
        exc_info : tuple, optional
            Exception info from sys.exc_info().
        """
        if not self.is_enabled:
            return
        
        # Get stack trace
        if exc_info:
            stack_trace = "".join(traceback.format_exception(*exc_info))
            error_type = exc_info[0].__name__ if exc_info[0] else error_type
            message = str(exc_info[1]) if exc_info[1] else message
        else:
            stack_trace = traceback.format_stack()
        
        report = CrashReport(
            timestamp=time.time(),
            error_type=error_type,
            message=message,
            stack_trace=stack_trace,
            context=context or {},
            system_info=self._system_info,
        )
        
        with self._lock:
            self._crash_reports.append(report)
    
    def flush(self) -> int:
        """Flush pending data to disk.
        
        Returns the number of items flushed.
        """
        with self._lock:
            count = len(self._crash_reports) + len(self._metrics)
            
            if count == 0:
                return 0
            
            # Write crash reports
            if self._crash_reports:
                self._write_crash_reports()
                self._crash_reports.clear()
            
            # Write metrics
            if self._metrics:
                self._write_metrics()
                self._metrics.clear()
            
            return count
    
    def get_crash_reports(self) -> List[Dict[str, Any]]:
        """Get all stored crash reports."""
        reports = []
        for report_file in self._data_dir.glob("crash_*.json"):
            try:
                with open(report_file) as f:
                    reports.append(json.load(f))
            except (OSError, json.JSONDecodeError):
                pass
        return reports
    
    def get_metrics(self, name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get stored metrics, optionally filtered by name."""
        metrics = []
        for metric_file in self._data_dir.glob("metrics_*.json"):
            try:
                with open(metric_file) as f:
                    data = json.load(f)
                    if name:
                        data = [m for m in data if m.get("name") == name]
                    metrics.extend(data)
            except (OSError, json.JSONDecodeError):
                pass
        return metrics
    
    def clear_all(self) -> int:
        """Clear all stored telemetry data.
        
        Returns the number of files deleted.
        """
        count = 0
        for file in self._data_dir.glob("*.json"):
            try:
                file.unlink()
                count += 1
            except OSError:
                pass
        return count
    
    def _collect_system_info(self) -> Dict[str, str]:
        """Collect non-PII system information."""
        return {
            "os": platform.system(),
            "os_version": platform.version(),
            "arch": platform.machine(),
            "python": platform.python_version(),
            # Hash the hostname for uniqueness without exposing it
            "host_id": hashlib.sha256(
                platform.node().encode()
            ).hexdigest()[:16],
        }
    
    def _write_crash_reports(self) -> None:
        """Write crash reports to disk."""
        timestamp = int(time.time() * 1000)
        filename = self._data_dir / f"crash_{timestamp}.json"
        
        data = [report.to_dict() for report in self._crash_reports]
        with open(filename, "w") as f:
            json.dump(data, f, indent=2)
    
    def _write_metrics(self) -> None:
        """Write metrics to disk."""
        timestamp = int(time.time() * 1000)
        filename = self._data_dir / f"metrics_{timestamp}.json"
        
        data = [metric.to_dict() for metric in self._metrics]
        with open(filename, "w") as f:
            json.dump(data, f, indent=2)
    
    def _start_flush_thread(self) -> None:
        """Start the auto-flush background thread."""
        if self._running:
            return
        
        self._running = True
        self._flush_thread = threading.Thread(
            target=self._flush_loop,
            daemon=True,
            name="telemetry-flush",
        )
        self._flush_thread.start()
    
    def _stop_flush_thread(self) -> None:
        """Stop the auto-flush background thread."""
        self._running = False
        if self._flush_thread and self._flush_thread.is_alive():
            self._flush_thread.join(timeout=2.0)
    
    def _flush_loop(self) -> None:
        """Background thread for periodic flushing."""
        while self._running:
            time.sleep(self._flush_interval)
            if self._running and self.is_enabled:
                self.flush()


__all__ = ["Telemetry", "TelemetryStatus", "CrashReport", "MetricPoint"]
