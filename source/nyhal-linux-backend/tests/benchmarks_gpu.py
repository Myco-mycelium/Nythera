"""benchmarks_gpu — Performance benchmarks for GPU rendering paths.

Measures the performance of:
- GBM buffer allocation and lifecycle
- EGL context creation and initialization
- Vulkan instance/device/swapchain creation
- Compositor surface management
- Full pipeline latency (GBM → render → display)

References:
    - NEXT_SESSION_PLAN: Priority 6 (Performance Benchmarks)
    - ADR-0026 Phase 3: GPU acceleration
"""

from __future__ import annotations

import os
import sys
import time
from typing import Dict, List, Optional

# Ensure the backend is importable
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def benchmark_gbm_lifecycle(iterations: int = 100) -> Dict:
    """Benchmark GBM device → surface → buffer → release → close."""
    from ui.gbm_codec import (
        is_available, open_device, close_device,
        create_surface, destroy_surface,
        lock_buffer, release_buffer, get_buffer_info
    )

    if not is_available():
        return {"error": "GBM crate not available"}

    results = {
        "iterations": iterations,
        "times": {},
    }

    # Benchmark device open/close
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        dev = open_device()
        if dev >= 0:
            close_device(dev)
        times.append(time.perf_counter() - t0)
    results["times"]["device_open_close"] = _stats(times)

    # Benchmark surface create/destroy
    dev = open_device()
    if dev < 0:
        return {"error": "Cannot open device"}
    try:
        times = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            surf = create_surface(dev, 1920, 1080)
            if surf >= 0:
                destroy_surface(surf)
            times.append(time.perf_counter() - t0)
        results["times"]["surface_create_destroy"] = _stats(times)

        # Benchmark buffer lock/release
        times = []
        for _ in range(iterations):
            surf = create_surface(dev, 1920, 1080)
            t0 = time.perf_counter()
            buf = lock_buffer(surf)
            if buf >= 0:
                release_buffer(buf)
            times.append(time.perf_counter() - t0)
            destroy_surface(surf)
        results["times"]["buffer_lock_release"] = _stats(times)

        # Benchmark full lifecycle
        times = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            surf = create_surface(dev, 1920, 1080)
            buf = lock_buffer(surf)
            if buf >= 0:
                release_buffer(buf)
            destroy_surface(surf)
            times.append(time.perf_counter() - t0)
        results["times"]["full_lifecycle"] = _stats(times)
    finally:
        close_device(dev)

    return results


def benchmark_egl_lifecycle(iterations: int = 100) -> Dict:
    """Benchmark EGL display → init → config → context → destroy → terminate."""
    from ui.egl_codec import (
        is_available, get_display, initialize, choose_config,
        create_context, destroy_context, terminate
    )

    if not is_available():
        return {"error": "EGL crate not available"}

    results = {
        "iterations": iterations,
        "times": {},
    }

    # Benchmark display + init
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        display = get_display()
        if display >= 0:
            initialize(display)
            terminate(display)
        times.append(time.perf_counter() - t0)
    results["times"]["display_init_terminate"] = _stats(times)

    # Benchmark config + context
    times = []
    for _ in range(iterations):
        display = get_display()
        if display >= 0:
            initialize(display)
            t0 = time.perf_counter()
            config = choose_config(display)
            ctx = create_context(display, config)
            if ctx >= 0:
                destroy_context(ctx)
            times.append(time.perf_counter() - t0)
            terminate(display)
    results["times"]["config_context"] = _stats(times)

    return results


def benchmark_vulkan_lifecycle(iterations: int = 100) -> Dict:
    """Benchmark Vulkan instance → device → swapchain → destroy."""
    from ui.vulkan_codec import (
        is_available, create_instance, destroy_instance,
        create_device, destroy_device,
        create_swapchain, destroy_swapchain
    )

    if not is_available():
        return {"error": "Vulkan crate not available"}

    results = {
        "iterations": iterations,
        "times": {},
    }

    # Benchmark instance create/destroy
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        inst = create_instance()
        if inst >= 0:
            destroy_instance(inst)
        times.append(time.perf_counter() - t0)
    results["times"]["instance_create_destroy"] = _stats(times)

    # Benchmark device create/destroy
    inst = create_instance()
    if inst < 0:
        return {"error": "Cannot create Vulkan instance"}
    try:
        times = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            dev = create_device(inst)
            if dev >= 0:
                destroy_device(dev)
            times.append(time.perf_counter() - t0)
        results["times"]["device_create_destroy"] = _stats(times)

        # Benchmark swapchain create/destroy
        dev = create_device(inst)
        if dev >= 0:
            times = []
            for _ in range(iterations):
                t0 = time.perf_counter()
                sc = create_swapchain(dev, 1920, 1080, 3)
                if sc >= 0:
                    destroy_swapchain(sc)
                times.append(time.perf_counter() - t0)
            results["times"]["swapchain_create_destroy"] = _stats(times)
            destroy_device(dev)
    finally:
        destroy_instance(inst)

    return results


def benchmark_compositor_lifecycle(iterations: int = 100) -> Dict:
    """Benchmark compositor start → output → surface → input → stop."""
    from ui.compositor_codec import (
        available, start, stop,
        add_output, create_surface, destroy_surface,
        process_input, commit_surface
    )

    if not available():
        return {"error": "Compositor crate not available"}

    results = {
        "iterations": iterations,
        "times": {},
    }

    # Benchmark start/stop
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        start()
        stop()
        times.append(time.perf_counter() - t0)
    results["times"]["start_stop"] = _stats(times)

    # Benchmark output/surface lifecycle
    times = []
    for _ in range(iterations):
        start()
        t0 = time.perf_counter()
        out = add_output(1920, 1080, "bench")
        surf = create_surface(0, 800, 600)
        if surf >= 0:
            destroy_surface(surf)
        times.append(time.perf_counter() - t0)
        stop()
    results["times"]["output_surface_lifecycle"] = _stats(times)

    return results


def _stats(times: List[float]) -> Dict:
    """Compute statistics from a list of timing measurements."""
    if not times:
        return {}
    times_us = [t * 1_000_000 for t in times]  # convert to microseconds
    times_us.sort()
    n = len(times_us)
    return {
        "count": n,
        "min_us": round(times_us[0], 1),
        "max_us": round(times_us[-1], 1),
        "mean_us": round(sum(times_us) / n, 1),
        "median_us": round(times_us[n // 2], 1),
        "p95_us": round(times_us[int(n * 0.95)], 1),
        "p99_us": round(times_us[int(n * 0.99)], 1),
    }


def run_all_benchmarks(iterations: int = 100) -> Dict:
    """Run all GPU benchmarks and return results."""
    results = {
        "iterations": iterations,
        "benchmarks": {},
    }

    benchmarks = [
        ("gbm", benchmark_gbm_lifecycle),
        ("egl", benchmark_egl_lifecycle),
        ("vulkan", benchmark_vulkan_lifecycle),
        ("compositor", benchmark_compositor_lifecycle),
    ]

    for name, fn in benchmarks:
        print(f"Running {name} benchmark ({iterations} iterations)...")
        try:
            result = fn(iterations)
            results["benchmarks"][name] = result
            if "error" not in result:
                print(f"  ✓ {name} complete")
            else:
                print(f"  ⚠ {name}: {result['error']}")
        except Exception as e:
            results["benchmarks"][name] = {"error": str(e)}
            print(f"  ✗ {name} failed: {e}")

    return results


def print_results(results: Dict) -> None:
    """Print benchmark results in a formatted table."""
    print()
    print("=" * 70)
    print("GPU Performance Benchmark Results")
    print("=" * 70)
    print(f"Iterations: {results['iterations']}")
    print()

    for name, data in results.get("benchmarks", {}).items():
        print(f"--- {name.upper()} ---")
        if "error" in data:
            print(f"  Error: {data['error']}")
            continue
        for metric, stats in data.get("times", {}).items():
            if not stats or "min_us" not in stats:
                print(f"  {metric}: (no data)")
                continue
            print(f"  {metric}:")
            print(f"    min: {stats['min_us']:.1f} µs  "
                  f"median: {stats['median_us']:.1f} µs  "
                  f"p95: {stats['p95_us']:.1f} µs  "
                  f"max: {stats['max_us']:.1f} µs")
        print()


def main():
    """CLI entry point for GPU benchmarks."""
    import argparse
    parser = argparse.ArgumentParser(description="GPU Performance Benchmarks")
    parser.add_argument("-n", "--iterations", type=int, default=100,
                        help="Number of iterations (default: 100)")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    args = parser.parse_args()

    results = run_all_benchmarks(args.iterations)

    if args.json:
        import json
        print(json.dumps(results, indent=2))
    else:
        print_results(results)


if __name__ == "__main__":
    main()
