"""multi_monitor — Multi-monitor output detection and management for Nyrqis.

Provides per-output rendering and window migration capabilities:

1. Detect connected displays via DRM
2. Create per-output surfaces
3. Manage workspace-to-output binding
4. Handle output hot-plug events

References:
    - NEXT_SESSION_PLAN: Priority 5 (Multi-Monitor Enhancements)
    - ADR-0026: Wayland display-server integration
"""

from __future__ import annotations

import ctypes
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class OutputStatus(Enum):
    """Display output status."""
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    ACTIVE = "active"


@dataclass
class OutputInfo:
    """Information about a display output."""
    id: int
    name: str
    width: int
    height: int
    refresh_rate: int  # mHz (e.g., 60000 for 60Hz)
    status: OutputStatus
    x: int = 0
    y: int = 0
    primary: bool = False


@dataclass
class WorkspaceBinding:
    """Binding between a workspace and an output."""
    workspace_id: int
    output_id: int
    surface_id: int = -1


class MultiMonitorManager:
    """Manages multiple display outputs.
    
    Provides:
    - Output detection and enumeration
    - Per-output surface management
    - Workspace-to-output binding
    - Output hot-plug handling
    """
    
    def __init__(self):
        self.outputs: Dict[int, OutputInfo] = {}
        self.bindings: List[WorkspaceBinding] = []
        self._next_output_id = 0
        self._compositor = None
    
    def _load_compositor(self) -> bool:
        """Load the compositor crate."""
        try:
            from ui import compositor_codec as comp
            if comp.available():
                self._compositor = comp
                return True
        except ImportError:
            pass
        return False
    
    def detect_outputs(self) -> List[OutputInfo]:
        """Detect connected display outputs.
        
        Returns a list of detected outputs.
        """
        outputs = []
        
        # Try to detect via compositor
        if self._compositor is None:
            self._load_compositor()
        
        if self._compositor:
            # Query compositor for outputs
            count = self._compositor.output_count()
            for i in range(count):
                # Get output info from compositor
                output = OutputInfo(
                    id=i,
                    name=f"output-{i}",
                    width=1920,  # default, will be updated
                    height=1080,
                    refresh_rate=60000,
                    status=OutputStatus.CONNECTED,
                )
                outputs.append(output)
        
        # If no outputs detected, create a virtual one
        if not outputs:
            output = OutputInfo(
                id=self._next_output_id,
                name="virtual-0",
                width=1920,
                height=1080,
                refresh_rate=60000,
                status=OutputStatus.CONNECTED,
                primary=True,
            )
            outputs.append(output)
            self._next_output_id += 1
        
        # Update output registry
        self.outputs.clear()
        for out in outputs:
            self.outputs[out.id] = out
        
        return outputs
    
    def add_output(self, width: int, height: int, name: str = "",
                   refresh_rate: int = 60000) -> OutputInfo:
        """Add a new output.
        
        Parameters
        ----------
        width : int
            Output width in pixels.
        height : int
            Output height in pixels.
        name : str
            Output name (optional).
        refresh_rate : int
            Refresh rate in mHz.
            
        Returns
        -------
        OutputInfo
            The created output.
        """
        output_id = self._next_output_id
        self._next_output_id += 1
        
        output = OutputInfo(
            id=output_id,
            name=name or f"output-{output_id}",
            width=width,
            height=height,
            refresh_rate=refresh_rate,
            status=OutputStatus.ACTIVE,
            primary=len(self.outputs) == 0,
        )
        
        self.outputs[output_id] = output
        logger.info("Added output %d: %dx%d@%dmHz", output_id, width, height, refresh_rate)
        
        return output
    
    def remove_output(self, output_id: int, migrate: bool = True) -> List[int]:
        """Remove an output and optionally migrate its workspaces.
        
        Parameters
        ----------
        output_id : int
            The output to remove.
        migrate : bool
            If True, migrate bound workspaces to the primary output.
            
        Returns
        -------
        list of int
            List of workspace IDs that were migrated.
        """
        if output_id not in self.outputs:
            return []
        
        migrated = []
        
        # Migrate bound workspaces before removing
        if migrate:
            primary = self.get_primary_output()
            if primary and primary.id != output_id:
                for binding in self.bindings[:]:
                    if binding.output_id == output_id:
                        binding.output_id = primary.id
                        migrated.append(binding.workspace_id)
                        logger.info("Migrated workspace %d to output %d",
                                   binding.workspace_id, primary.id)
        
        # Remove any remaining workspace bindings
        self.bindings = [b for b in self.bindings if b.output_id != output_id]
        
        # Remove the output
        del self.outputs[output_id]
        logger.info("Removed output %d (migrated %d workspaces)", output_id, len(migrated))
        
        return migrated
    
    def bind_workspace(self, workspace_id: int, output_id: int) -> bool:
        """Bind a workspace to an output.
        
        Returns True if the binding was created, False if output not found.
        """
        if output_id not in self.outputs:
            logger.error("Output %d not found", output_id)
            return False
        
        # Remove existing binding for this workspace
        self.bindings = [b for b in self.bindings if b.workspace_id != workspace_id]
        
        binding = WorkspaceBinding(
            workspace_id=workspace_id,
            output_id=output_id,
        )
        self.bindings.append(binding)
        
        logger.info("Bound workspace %d to output %d", workspace_id, output_id)
        return True
    
    def get_output_for_workspace(self, workspace_id: int) -> Optional[OutputInfo]:
        """Get the output bound to a workspace.
        
        Returns the output info, or None if not bound.
        """
        for binding in self.bindings:
            if binding.workspace_id == workspace_id:
                return self.outputs.get(binding.output_id)
        return None
    
    def get_primary_output(self) -> Optional[OutputInfo]:
        """Get the primary output."""
        for output in self.outputs.values():
            if output.primary:
                return output
        # Fall back to first output
        if self.outputs:
            return next(iter(self.outputs.values()))
        return None
    
    def get_total_resolution(self) -> tuple:
        """Get the total resolution across all outputs."""
        if not self.outputs:
            return (1920, 1080)
        
        max_x = 0
        max_y = 0
        for output in self.outputs.values():
            right = output.x + output.width
            bottom = output.y + output.height
            if right > max_x:
                max_x = right
            if bottom > max_y:
                max_y = bottom
        
        return (max_x, max_y)
    
    def get_output_count(self) -> int:
        """Get the number of active outputs."""
        return len([o for o in self.outputs.values()
                   if o.status in (OutputStatus.CONNECTED, OutputStatus.ACTIVE)])
