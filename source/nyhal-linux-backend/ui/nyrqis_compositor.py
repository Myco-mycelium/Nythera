"""nyrqis_compositor — Integrated Wayland compositor for Nyrqis.

Combines the Wayland socket server, compositor codec, and render pipeline
into a single cohesive compositor implementation:

1. Starts the Wayland socket for client connections
2. Initializes the compositor state via the Rust codec
3. Sets up the GPU render pipeline (GBM + EGL + DRM)
4. Dispatches Wayland protocol messages from clients
5. Renders frames and presents them to the display

This is the main entry point for running a Nyrqis desktop environment.

References:
    - ADR-0026: Wayland display-server integration
    - ADR-0010: Vulkan as native graphics API
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CompositorConfig:
    """Configuration for the integrated compositor."""
    # Display
    width: int = 1920
    height: int = 1080
    refresh_rate: int = 60000
    
    # Wayland socket
    socket_path: str = "/tmp/wayland-0"
    
    # GPU
    render_node: str = "/dev/dri/renderD128"
    use_gbm: bool = True
    use_egl: bool = True
    use_vulkan: bool = False
    
    # Performance
    frame_rate_limit: int = 60  # FPS
    vsync: bool = True
    
    # Debugging
    headless: bool = False
    verbose: bool = False


class NyrqisCompositor:
    """Integrated Wayland compositor for Nyrqis.
    
    Usage:
        compositor = NyrqisCompositor()
        compositor.start()
        # ... compositor runs until stopped ...
        compositor.stop()
    """
    
    def __init__(self, config: Optional[CompositorConfig] = None):
        self.config = config or CompositorConfig()
        self._socket_server = None
        self._compositor_started = False
        self._render_pipeline = None
        self._running = False
        self._frame_count = 0
        self._start_time = 0.0
        
        # Signal handlers
        self._original_sigint = None
        self._original_sigterm = None
    
    def start(self) -> bool:
        """Start the compositor.
        
        Returns True on success, False on failure.
        """
        if self._running:
            logger.warning("Compositor already running")
            return False
        
        logger.info("Starting Nyrqis compositor (%dx%d@%dHz)",
                   self.config.width, self.config.height, self.config.refresh_rate)
        self._start_time = time.monotonic()
        
        # Set up signal handlers for clean shutdown
        self._setup_signals()
        
        # Step 1: Start Wayland socket server
        if not self.config.headless:
            if not self._start_socket_server():
                logger.error("Failed to start socket server")
                return False
        
        # Step 2: Start compositor via Rust codec
        if not self._start_compositor():
            logger.error("Failed to start compositor")
            self._stop_socket_server()
            return False
        
        # Step 3: Initialize render pipeline (if not headless)
        if not self.config.headless:
            if not self._init_render_pipeline():
                logger.warning("Render pipeline not available (headless mode)")
        
        self._running = True
        logger.info("Nyrqis compositor started successfully")
        return True
    
    def stop(self):
        """Stop the compositor."""
        if not self._running:
            return
        
        logger.info("Stopping Nyrqis compositor (rendered %d frames)", self._frame_count)
        
        # Clean up render pipeline
        if self._render_pipeline:
            self._render_pipeline.cleanup()
            self._render_pipeline = None
        
        # Stop compositor
        self._stop_compositor()
        
        # Stop socket server
        self._stop_socket_server()
        
        # Restore signal handlers
        self._restore_signals()
        
        self._running = False
        
        # Print stats
        elapsed = time.monotonic() - self._start_time if self._start_time else 0
        fps = self._frame_count / elapsed if elapsed > 0 else 0
        logger.info("Compositor stats: %d frames, %.1f fps avg, %.1fs elapsed",
                   self._frame_count, fps, elapsed)
    
    def render_frame(self) -> bool:
        """Render a single frame.
        
        Returns True on success, False on failure.
        """
        if not self._running:
            return False
        
        # Render via pipeline
        if self._render_pipeline:
            if not self._render_pipeline.render_frame():
                return False
        
        self._frame_count += 1
        return True
    
    def _start_socket_server(self) -> bool:
        """Start the Wayland socket server."""
        try:
            from ui.wayland_socket import WaylandSocketServer
            
            self._socket_server = WaylandSocketServer(self.config.socket_path)
            
            # Set up callbacks
            self._socket_server.set_surface_callback(self._on_surface_created)
            self._socket_server.set_buffer_callback(self._on_buffer_attached)
            
            # Add default output
            self._socket_server.add_output(
                self.config.width,
                self.config.height,
                "default",
                self.config.refresh_rate,
            )
            
            if not self._socket_server.start():
                logger.error("Failed to start socket server")
                return False
            
            logger.info("Wayland socket server started: %s", self.config.socket_path)
            return True
            
        except ImportError as exc:
            logger.error("wayland_socket not available: %s", exc)
            return False
    
    def _stop_socket_server(self):
        """Stop the Wayland socket server."""
        if self._socket_server:
            self._socket_server.stop()
            self._socket_server = None
    
    def _start_compositor(self) -> bool:
        """Start the compositor via the Rust codec."""
        try:
            from ui import compositor_codec as comp
            
            if not comp.available():
                logger.warning("Compositor crate not available")
                return True  # Continue without Rust compositor
            
            # Start compositor
            result = comp.start()
            if result != 0:
                logger.error("Failed to start compositor: %s", comp.last_error())
                return False
            
            # Add output
            comp.add_output(self.config.width, self.config.height, "default")
            
            self._compositor_started = True
            logger.info("Compositor started via Rust codec")
            return True
            
        except ImportError as exc:
            logger.warning("Compositor codec not available: %s", exc)
            return True  # Continue without Rust compositor
    
    def _stop_compositor(self):
        """Stop the compositor via the Rust codec."""
        if self._compositor_started:
            try:
                from ui import compositor_codec as comp
                if comp.available():
                    comp.stop()
            except ImportError:
                pass
            self._compositor_started = False
    
    def _init_render_pipeline(self) -> bool:
        """Initialize the GPU render pipeline."""
        try:
            from ui.render_pipeline import RenderPipeline, RenderConfig
            
            render_config = RenderConfig(
                width=self.config.width,
                height=self.config.height,
                render_node=self.config.render_node,
                use_gbm=self.config.use_gbm,
                use_egl=self.config.use_egl,
                use_drm=True,
            )
            
            self._render_pipeline = RenderPipeline(render_config)
            
            if not self._render_pipeline.initialize():
                logger.warning("Failed to initialize render pipeline")
                return False
            
            logger.info("Render pipeline initialized")
            return True
            
        except ImportError as exc:
            logger.warning("Render pipeline not available: %s", exc)
            return False
    
    def _on_surface_created(self, surface):
        """Handle surface creation event."""
        logger.debug("Surface created: id=%d", surface.id)
        
        # Create surface in compositor
        try:
            from ui import compositor_codec as comp
            if comp.available():
                comp.create_surface(surface.client_id, surface.width or 800, surface.height or 600)
        except ImportError:
            pass
    
    def _on_buffer_attached(self, surface):
        """Handle buffer attachment event."""
        logger.debug("Buffer attached to surface %d", surface.id)
        
        # Commit surface
        try:
            from ui import compositor_codec as comp
            if comp.available():
                comp.commit_surface(surface.id)
        except ImportError:
            pass
    
    def _setup_signals(self):
        """Set up signal handlers for clean shutdown."""
        def signal_handler(signum, frame):
            logger.info("Received signal %d, shutting down...", signum)
            self.stop()
            sys.exit(0)
        
        self._original_sigint = signal.signal(signal.SIGINT, signal_handler)
        self._original_sigterm = signal.signal(signal.SIGTERM, signal_handler)
    
    def _restore_signals(self):
        """Restore original signal handlers."""
        if self._original_sigint is not None:
            signal.signal(signal.SIGINT, self._original_sigint)
        if self._original_sigterm is not None:
            signal.signal(signal.SIGTERM, self._original_sigterm)
    
    def get_stats(self) -> dict:
        """Get compositor statistics."""
        elapsed = time.monotonic() - self._start_time if self._start_time else 0
        fps = self._frame_count / elapsed if elapsed > 0 else 0
        
        stats = {
            "running": self._running,
            "frame_count": self._frame_count,
            "elapsed_seconds": round(elapsed, 3),
            "fps": round(fps, 2),
            "config": {
                "width": self.config.width,
                "height": self.config.height,
                "refresh_rate": self.config.refresh_rate,
                "socket_path": self.config.socket_path,
            },
        }
        
        # Add client/surface counts from socket server
        if self._socket_server:
            stats["clients"] = self._socket_server.get_client_count()
            stats["surfaces"] = self._socket_server.get_surface_count()
            stats["outputs"] = self._socket_server.get_output_count()
        
        return stats
    
    def __enter__(self):
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False


def main():
    """Main entry point for the Nyrqis compositor."""
    import argparse
    
    parser = argparse.ArgumentParser(
        prog="nyrqis_compositor",
        description="Nyrqis integrated Wayland compositor",
    )
    parser.add_argument("--width", type=int, default=1920, help="Output width")
    parser.add_argument("--height", type=int, default=1080, help="Output height")
    parser.add_argument("--refresh-rate", type=int, default=60000, help="Refresh rate (mHz)")
    parser.add_argument("--socket", default="/tmp/wayland-0", help="Wayland socket path")
    parser.add_argument("--render-node", default="/dev/dri/renderD128", help="DRM render node")
    parser.add_argument("--headless", action="store_true", help="Run headless (no display)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    
    args = parser.parse_args()
    
    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    
    # Create config
    config = CompositorConfig(
        width=args.width,
        height=args.height,
        refresh_rate=args.refresh_rate,
        socket_path=args.socket,
        render_node=args.render_node,
        headless=args.headless,
        verbose=args.verbose,
    )
    
    # Run compositor
    with NyrqisCompositor(config) as compositor:
        logger.info("Compositor running. Press Ctrl+C to stop.")
        
        # Main loop (render at target frame rate)
        frame_interval = 1.0 / config.refresh_rate
        try:
            while compositor._running:
                compositor.render_frame()
                
                # Frame rate limiting
                if not config.headless:
                    time.sleep(frame_interval)
                else:
                    # Headless mode: render one frame and exit
                    break
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
