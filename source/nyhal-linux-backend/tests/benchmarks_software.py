"""benchmarks_software — PIL software rendering baseline benchmark.

Measures software rendering performance as a baseline for comparison
with GPU-accelerated rendering paths.

Usage:
    python3 -m tests.benchmarks_software
    python3 -m tests.benchmarks_software --iterations 1000
"""

from __future__ import annotations

import os
import struct
import sys
import time
from typing import Dict

# Ensure the backend is importable
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _measure(func, iterations: int = 1000) -> dict:
    """Measure function execution time."""
    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        func()
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1_000_000)  # microseconds
    
    times.sort()
    n = len(times)
    
    return {
        "median": round(times[n // 2], 2),
        "mean": round(sum(times) / n, 2),
        "min": round(times[0], 2),
        "max": round(times[-1], 2),
        "p95": round(times[int(n * 0.95)], 2),
        "p99": round(times[int(n * 0.99)], 2),
    }


def benchmark_pil_render(iterations: int = 1000) -> dict:
    """Benchmark PIL image rendering."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return {"error": "PIL not available"}
    
    results = {}
    
    # Create image
    def create_image():
        img = Image.new("RGBA", (1920, 1080), (30, 30, 30, 255))
        return img
    
    results["create_image"] = _measure(create_image, iterations)
    
    # Draw rectangle
    def draw_rectangle():
        img = Image.new("RGBA", (1920, 1080), (30, 30, 30, 255))
        draw = ImageDraw.Draw(img)
        draw.rectangle([100, 100, 500, 400], fill=(255, 0, 0, 255))
    
    results["draw_rectangle"] = _measure(draw_rectangle, iterations)
    
    # Draw text
    def draw_text():
        img = Image.new("RGBA", (1920, 1080), (30, 30, 30, 255))
        draw = ImageDraw.Draw(img)
        draw.text((100, 100), "Hello, Nyrqis!", fill=(255, 255, 255, 255))
    
    results["draw_text"] = _measure(draw_text, iterations)
    
    # Composite images
    def composite():
        bg = Image.new("RGBA", (1920, 1080), (30, 30, 30, 255))
        fg = Image.new("RGBA", (400, 300), (255, 0, 0, 128))
        bg.paste(fg, (100, 100), fg)
    
    results["composite"] = _measure(composite, iterations)
    
    # Convert to bytes
    def to_bytes():
        img = Image.new("RGBA", (1920, 1080), (30, 30, 30, 255))
        data = img.tobytes()
    
    results["to_bytes"] = _measure(to_bytes, iterations)
    
    return results


def benchmark_raw_pixels(iterations: int = 1000) -> dict:
    """Benchmark raw pixel operations (no PIL)."""
    results = {}
    
    width, height = 1920, 1080
    
    # Create buffer
    def create_buffer():
        buf = bytearray(width * height * 4)
        return buf
    
    results["create_buffer"] = _measure(create_buffer, iterations)
    
    # Fill with color
    def fill_color():
        buf = bytearray(width * height * 4)
        pixel = struct.pack("BBBB", 30, 30, 30, 255)
        for y in range(height):
            offset = y * width * 4
            for x in range(width):
                buf[offset:offset+4] = pixel
                offset += 4
    
    results["fill_color"] = _measure(fill_color, iterations // 10)
    
    # Copy buffer
    def copy_buffer():
        src = bytearray(width * height * 4)
        dst = bytearray(width * height * 4)
        dst[:] = src
    
    results["copy_buffer"] = _measure(copy_buffer, iterations)
    
    return results


def benchmark_shm_buffer(iterations: int = 1000) -> dict:
    """Benchmark SHM buffer operations."""
    try:
        from ui.shm_buffer import ShmManager, WL_SHM_FORMAT_ARGB8888
    except ImportError:
        return {"error": "SHM buffer not available"}
    
    results = {}
    
    # Create region
    def create_region():
        s = ShmManager()
        r = s.create_region(1920 * 1080 * 4)
        s.cleanup()
    
    results["create_region"] = _measure(create_region, iterations // 10)
    
    # Create pool
    def create_pool():
        s = ShmManager()
        r = s.create_region(1920 * 1080 * 4)
        if r:
            s.create_pool(r)
        s.cleanup()
    
    results["create_pool"] = _measure(create_pool, iterations // 10)
    
    # Create buffer
    def create_buffer():
        s = ShmManager()
        r = s.create_region(1920 * 1080 * 4)
        if r:
            p = s.create_pool(r)
            if p:
                s.create_buffer(p, 0, 1920, 1080, 1920 * 4, WL_SHM_FORMAT_ARGB8888)
        s.cleanup()
    
    results["create_buffer"] = _measure(create_buffer, iterations // 10)
    
    # Fill buffer
    def fill_buffer():
        s = ShmManager()
        r = s.create_region(1920 * 1080 * 4)
        if r:
            p = s.create_pool(r)
            if p:
                b = s.create_buffer(p, 0, 1920, 1080, 1920 * 4, WL_SHM_FORMAT_ARGB8888)
                if b:
                    s.fill_buffer(b, 30, 30, 30, 255)
        s.cleanup()
    
    results["fill_buffer"] = _measure(fill_buffer, iterations // 10)
    return results


def run_all_benchmarks(iterations: int = 1000) -> dict:
    """Run all software rendering benchmarks."""
    print(f"\n{'='*60}")
    print(f"  Nyrqis Software Rendering Benchmarks ({iterations} iterations)")
    print(f"{'='*60}\n")
    
    all_results = {}
    
    benchmarks = [
        ("PIL Rendering", benchmark_pil_render),
        ("Raw Pixels", benchmark_raw_pixels),
        ("SHM Buffer", benchmark_shm_buffer),
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
    """Print a summary table."""
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
    
    parser = argparse.ArgumentParser(description="Nyrqis Software Rendering Benchmarks")
    parser.add_argument("--iterations", "-n", type=int, default=1000,
                       help="Number of iterations (default: 1000)")
    args = parser.parse_args()
    
    results = run_all_benchmarks(args.iterations)
    print_summary(results)
