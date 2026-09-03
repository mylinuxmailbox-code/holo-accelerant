#!/usr/bin/env python3
"""
Benchmark harness for comparing pip, uv, and accelero installation times.

Usage:
    python benchmarks/run_benchmark.py --packages requests numpy flask
    python benchmarks/run_benchmark.py --requirements requirements.txt
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


class Benchmark:
    """Benchmark harness for package installers."""

    def __init__(self, venv_dir: Path):
        self.venv_dir = venv_dir
        self.results: dict[str, dict[str, Any]] = {}

    def create_venv(self) -> Path:
        """Create a fresh virtual environment."""
        if self.venv_dir.exists():
            shutil.rmtree(self.venv_dir)
        self.venv_dir.mkdir(parents=True)
        subprocess.run(
            [sys.executable, "-m", "venv", str(self.venv_dir)],
            check=True,
            capture_output=True,
        )
        return self.venv_dir / "bin"

    def pip_install(self, packages: list[str]) -> dict[str, float]:
        """Install using pip."""
        start = time.perf_counter()
        try:
            subprocess.run(
                [self.venv_dir / "bin" / "pip", "install", "--no-cache-dir"] + packages,
                check=True,
                capture_output=True,
                timeout=300,
            )
        except Exception as e:
            return {"error": str(e), "time": time.perf_counter() - start}
        return {"time": time.perf_counter() - start}

    def uv_install(self, packages: list[str]) -> dict[str, float]:
        """Install using uv."""
        uv_path = shutil.which("uv")
        if not uv_path:
            return {"error": "uv not found"}

        start = time.perf_counter()
        try:
            subprocess.run(
                [uv_path, "pip", "install", "--no-cache"] + packages,
                check=True,
                capture_output=True,
                timeout=300,
            )
        except Exception as e:
            return {"error": str(e), "time": time.perf_counter() - start}
        return {"time": time.perf_counter() - start}

    def accelero_install(self, packages: list[str]) -> dict[str, float]:
        """Install using accelero."""
        accelero_path = Path(__file__).parent.parent / "src" / "accelero" / "cli" / "main.py"
        if not accelero_path.exists():
            accelero_path = shutil.which("accelero")
        if not accelero_path:
            return {"error": "accelero not found"}

        start = time.perf_counter()
        try:
            subprocess.run(
                [sys.executable, str(accelero_path), "install"] + packages,
                check=True,
                capture_output=True,
                timeout=300,
                cwd=str(Path(__file__).parent.parent),
            )
        except Exception as e:
            return {"error": str(e), "time": time.perf_counter() - start}
        return {"time": time.perf_counter() - start}


def run_benchmark(
    packages: list[str],
    iterations: int = 3,
    compare_with: list[str] | None = None,
) -> dict[str, Any]:
    """Run benchmark comparing installers."""
    if compare_with is None:
        compare_with = ["pip", "uv", "accelero"]

    results: dict[str, list[float]] = {}

    for iteration in range(iterations):
        print(f"\n=== Iteration {iteration + 1}/{iterations} ===")

        with tempfile.TemporaryDirectory(prefix="accelero-bench-") as tmpdir:
            venv = Path(tmpdir) / "venv"
            bench = Benchmark(venv)
            bin_dir = bench.create_venv()

            for tool in compare_with:
                print(f"\n  Testing {tool}...")
                venv = Path(tmpdir) / f"venv_{tool}_{iteration}"
                if venv.exists():
                    shutil.rmtree(venv)

                # Create fresh venv for each tool
                subprocess.run(
                    [sys.executable, "-m", "venv", str(venv)],
                    check=True,
                    capture_output=True,
                )
                bin_dir = venv / "bin"

                start = time.perf_counter()
                try:
                    if tool == "pip":
                        result = subprocess.run(
                            [bin_dir / "pip", "install", "--no-cache-dir"] + packages,
                            check=True,
                            capture_output=True,
                            timeout=300,
                        )
                    elif tool == "uv":
                        result = subprocess.run(
                            ["uv", "pip", "install", "--no-cache"] + packages,
                            check=True,
                            capture_output=True,
                            timeout=300,
                        )
                    elif tool == "accelero":
                        result = subprocess.run(
                            [sys.executable, "-m", "accelero", "install"] + packages,
                            check=True,
                            capture_output=True,
                            timeout=300,
                            cwd=str(Path(__file__).parent.parent),
                        )
                    elapsed = time.perf_counter() - start
                except subprocess.TimeoutExpired:
                    elapsed = 300
                    print(f"  {tool}: TIMEOUT (>{elapsed}s)")
                except Exception as e:
                    elapsed = time.perf_counter() - start
                    print(f"  {tool}: ERROR - {e}")

                if tool not in results:
                    results[tool] = []
                results[tool].append(elapsed)
                print(f"  {tool}: {elapsed:.3f}s")

    # Calculate statistics
    stats = {}
    for tool, times in results.items():
        if times:
            avg = sum(times) / len(times)
            min_t = min(times)
            max_t = max(times)
            stats[tool] = {
                "iterations": len(times),
                "avg": avg,
                "min": min_t,
                "max": max_t,
                "times": times,
            }

    return stats


def main():
    parser = argparse.ArgumentParser(description="Benchmark package installers")
    parser.add_argument("--packages", nargs="+", help="Packages to install")
    parser.add_argument("--requirements", type=Path, help="Requirements file")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--compare", nargs="+", default=["pip", "uv"])
    parser.add_argument("--output", type=Path, help="Output JSON file")
    args = parser.parse_args()

    packages = []
    if args.packages:
        packages.extend(args.packages)
    if args.requirements:
        if not args.requirements.exists():
            print(f"Requirements file not found: {args.requirements}")
            return 1
        content = args.requirements.read_text()
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            packages.append(line)

    if not packages:
        print("No packages specified")
        return 1

    print(f"Benchmarking installation of: {', '.join(packages[:5])}{'...' if len(packages) > 5 else ''}")
    print(f"Comparing: {', '.join(args.compare)}")
    print(f"Iterations: {args.iterations}")

    stats = run_benchmark(packages, args.iterations, args.compare)

    print("\n" + "=" * 50)
    print("BENCHMARK RESULTS")
    print("=" * 50)

    for tool, data in sorted(stats.items(), key=lambda x: x[1]["avg"]):
        print(f"\n{tool}:")
        print(f"  Average: {data['avg']:.3f}s")
        print(f"  Min:     {data['min']:.3f}s")
        print(f"  Max:     {data['max']:.3f}s")

    # Calculate speedups
    if "pip" in stats and "accelero" in stats:
        speedup = stats["pip"]["avg"] / stats["accelero"]["avg"]
        print(f"\naccelero vs pip: {speedup:.2f}x {'faster' if speedup > 1 else 'slower'}")

    if "uv" in stats and "accelero" in stats:
        speedup = stats["uv"]["avg"] / stats["accelero"]["avg"]
        print(f"accelero vs uv:  {speedup:.2f}x {'faster' if speedup > 1 else 'slower'}")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(stats, f, indent=2)
        print(f"\nResults saved to: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
