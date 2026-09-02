"""benchmarks_full — Comprehensive GPU performance benchmarks for Nyrqis.

Measures rendering performance across all display paths:
1. Software rendering (PIL) baseline
2. GBM buffer allocation
3. EGL display/config/context
4. Vulkan instance/device
5. Compositor start/stop
6. DRM device open
7. Wayland socket operations
8. Full render pipeline (GBM + EGL)

Usage:
    python3 -m tests.benchmarks_full
    python3 -m tests.benchmarks_full --iterations 1000
"""

from __future__ import annotations

import os
import sys
import time
from typing import Dict, List

# Ensure the backend is importable
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _measure(func, iterations: int = 1000) -> dict:
    """Measure function execution time.
    
    Returns dict with median, mean, min, max, p95, p99 in microseconds.
    """
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        func()
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1_000_000)  # Convert to microseconds
    
    times.sort()
    n = len(times)
    median = times[n // 2]
    mean = sum(times) / n
    minimum = times[0]
    maximum = times[-1]
    p95 = times[int(n * 0.95)]
    p99 = times[int(n * 0.99)]
    
    return {
        "median": round(median, 2),
        "mean": round(mean, 2),
        "min": round(minimum, 2),
        "max": round(maximum, 2),
        "p95": round(p95, 2),
        "p99": round(p99, 2),
    }


def benchmark_gbm(iterations: int = 1000) -> dict:
    """Benchmark GBM buffer allocation."""
    try:
        from ui import gbm_codec as gbm
        if not gbm.is_available():
            return {"error": "GBM not available"}
    except ImportError:
        return {"error": "GBM not importable"}
    
    results = {}
    
    # Device open/close
    def open_close():
        dev = gbm.open_device()
        if dev >= 0:
            gbm.close_device(dev)
    
    results["device_open_close"] = _measure(open_close, iterations)
    
    # Surface create/destroy
    def surface_lifecycle():
        dev = gbm.open_device()
        if dev >= 0:
            surf = gbm.create_surface(dev, 1920, 1080)
            if surf >= 0:
                gbm.destroy_surface(surf)
            gbm.close_device(dev)
    
    results["surface_lifecycle"] = _measure(surface_lifecycle, iterations // 10)
    
    # Buffer lock/release
    dev = gbm.open_device()
    if dev >= 0:
        surf = gbm.create_surface(dev, 1920, 1080)
        if surf >= 0:
            def buffer_lock_release():
                buf = gbm.lock_buffer(surf)
                if buf >= 0:
                    gbm.release_buffer(buf)
            
            results["buffer_lock_release"] = _measure(buffer_lock_release, iterations // 10)
            gbm.destroy_surface(surf)
        gbm.close_device(dev)
    
    return results


def benchmark_egl(iterations: int = 1000) -> dict:
    """Benchmark EGL operations."""
    try:
        from ui import egl_codec as egl
        if not egl.is_available():
            return {"error": "EGL not available"}
    except ImportError:
        return {"error": "EGL not importable"}
    
    results = {}
    
    # Display init/terminate
    def display_lifecycle():
        disp = egl.get_display()
        if disp >= 0:
            egl.initialize(disp)
            egl.terminate(disp)
    
    results["display_init_terminate"] = _measure(display_lifecycle, iterations)
    
    # Config selection
    def config_selection():
        disp = egl.get_display()
        if disp >= 0:
            egl.initialize(disp)
            egl.choose_config(disp)
            egl.terminate(disp)
    
    results["config_selection"] = _measure(config_selection, iterations // 10)
    
    # Context creation
    def context_creation():
        disp = egl.get_display()
        if disp >= 0:
            egl.initialize(disp)
            config = egl.choose_config(disp)
            if config >= 0:
                ctx = egl.create_context(disp, config)
                if ctx >= 0:
                    egl.destroy_context(ctx)
            egl.terminate(disp)
    
    results["context_creation"] = _measure(context_creation, iterations // 10)
    
    # Full lifecycle
    def full_lifecycle():
        disp = egl.get_display()
        if disp >= 0:
            egl.initialize(disp)
            config = egl.choose_config(disp)
            if config >= 0:
                surf = egl.create_window_surface(disp, config, 1920, 1080)
                ctx = egl.create_context(disp, config)
                if ctx >= 0:
                    egl.destroy_context(ctx)
                if surf >= 0:
                    egl.destroy_surface(surf)
            egl.terminate(disp)
    
    results["full_lifecycle"] = _measure(full_lifecycle, iterations // 10)
    
    return results


def benchmark_vulkan(iterations: int = 1000) -> dict:
    """Benchmark Vulkan operations."""
    try:
        from ui import vulkan_codec as vk
        if not vk.is_available():
            return {"error": "Vulkan not available"}
    except ImportError:
        return {"error": "Vulkan not importable"}
    
    results = {}
    
    # Instance create/destroy
    def instance_lifecycle():
        inst = vk.create_instance()
        if inst >= 0:
            vk.destroy_instance(inst)
    
    results["instance_create_destroy"] = _measure(instance_lifecycle, iterations)
    
    # Device create/destroy
    def device_lifecycle():
        inst = vk.create_instance()
        if inst >= 0:
            dev = vk.create_device(inst)
            if dev >= 0:
                vk.destroy_device(dev)
            vk.destroy_instance(inst)
    
    results["device_create_destroy"] = _measure(device_lifecycle, iterations // 10)
    
    # Full lifecycle
    def full_lifecycle():
        inst = vk.create_instance()
        if inst >= 0:
            dev = vk.create_device(inst)
            if dev >= 0:
                sc = vk.create_swapchain(dev, 1920, 1080, 3)
                if sc >= 0:
                    vk.destroy_swapchain(sc)
                vk.destroy_device(dev)
            vk.destroy_instance(inst)
    
    results["full_lifecycle"] = _measure(full_lifecycle, iterations // 10)
    
    return results


def benchmark_compositor(iterations: int = 1000) -> dict:
    """Benchmark compositor operations."""
    try:
        from ui import compositor_codec as comp
        if not comp.available():
            return {"error": "Compositor not available"}
    except ImportError:
        return {"error": "Compositor not importable"}
    
    results = {}
    
    # Start/stop
    def start_stop():
        comp.start()
        comp.stop()
    
    results["start_stop"] = _measure(start_stop, iterations)
    
    # Output add/remove
    def output_lifecycle():
        comp.start()
        out = comp.add_output(1920, 1080, "bench")
        comp.stop()
    
    results["output_lifecycle"] = _measure(output_lifecycle, iterations // 10)
    
    # Surface create/destroy
    def surface_lifecycle():
        comp.start()
        surf = comp.create_surface(0, 800, 600)
        if surf >= 0:
            comp.destroy_surface(surf)
        comp.stop()
    
    results["surface_lifecycle"] = _measure(surface_lifecycle, iterations // 10)
    
    return results


def benchmark_drm(iterations: int = 1000) -> dict:
    """Benchmark DRM operations."""
    try:
        from ui import drm_codec as drm
        if not drm.is_available():
            return {"error": "DRM not available"}
    except ImportError:
        return {"error": "DRM not importable"}
    
    results = {}
    
    # Device open/close
    def open_close():
        dev = drm.open_device()
        if dev >= 0:
            drm.close_device(dev)
    
    results["device_open_close"] = _measure(open_close, iterations)
    
    return results


def benchmark_render_pipeline(iterations: int = 100) -> dict:
    """Benchmark the full render pipeline."""
    try:
        from ui.render_pipeline import RenderPipeline, RenderConfig
    except ImportError:
        return {"error": "Render pipeline not importable"}
    
    results = {}
    
    def pipeline_lifecycle():
        config = RenderConfig(width=640, height=480)
        with RenderPipeline(config) as pipeline:
            pipeline.render_frame()
    
    results["pipeline_lifecycle"] = _measure(pipeline_lifecycle, iterations)
    
    return results


def run_all_benchmarks(iterations: int = 1000) -> dict:
    """Run all benchmarks and return results."""
    print(f"\n{'='*60}")
    print(f"  Nyrqis GPU Performance Benchmarks ({iterations} iterations)")
    print(f"{'='*60}\n")
    
    all_results = {}
    
    benchmarks = [
        ("GBM", benchmark_gbm),
        ("EGL", benchmark_egl),
        ("Vulkan", benchmark_vulkan),
        ("Compositor", benchmark_compositor),
        ("DRM", benchmark_drm),
        ("Render Pipeline", benchmark_render_pipeline),
    ]
    
    for name, bench_func in benchmarks:
        print(f"Running {name} benchmarks...")
        try:
            results = bench_func(iterations)
            all_results[name] = results
            
            for op, stats in results.items():
                if isinstance(stats, dict) and "median" in stats:
                    print(f"  {op}: {stats['median']:.2f}µs (p95={stats['p95']:.2f}µs)")
        except Exception as exc:
            print(f"  Error: {exc}")
            all_results[name] = {"error": str(exc)}
        print()
    
    return all_results


def print_summary(results: dict):
    """Print a summary table of benchmark results."""
    print(f"\n{'='*60}")
    print(f"  Summary")
    print(f"{'='*60}\n")
    
    print(f"{'Operation':<30} {'Median':>10} {'P95':>10} {'Min':>10} {'Max':>10}")
    print("-" * 70)
    
    for category, ops in results.items():
        if isinstance(ops, dict) and not ops.get("error"):
            print(f"\n{category}:")
            for op, stats in ops.items():
                if isinstance(stats, dict) and "median" in stats:
                    print(f"  {op:<28} {stats['median']:>8.2f}µs {stats['p95']:>8.2f}µs "
                          f"{stats['min']:>8.2f}µs {stats['max']:>8.2f}µs")
    
    print()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Nyrqis GPU Performance Benchmarks")
    parser.add_argument("--iterations", "-n", type=int, default=1000,
                       help="Number of iterations (default: 1000)")
    args = parser.parse_args()
    
    results = run_all_benchmarks(args.iterations)
    print_summary(results)
