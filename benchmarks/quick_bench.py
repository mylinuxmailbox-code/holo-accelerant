#!/usr/bin/env python3
"""
Real benchmark comparing pip, uv, and accelero on identical setups.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REQUIREMENTS = """\
requests
click
pyyaml
six
"""

# Multiple scenarios
SCENARIOS = {
    "small (4 pkgs, no deps)": ["requests", "click", "pyyaml", "six"],
    "scientific": ["numpy"],  # Single scientific
    "web-stack": ["flask", "jinja2", "werkzeug", "itsdangerous"],
}


def create_venv(path: Path) -> None:
    """Create a fresh virtual environment."""
    subprocess.run(
        [sys.executable, "-m", "venv", str(path)],
        check=True,
        capture_output=True,
    )


def run_with_pip(venv: Path, packages: list[str]) -> float:
    """Install using pip, return elapsed time."""
    start = time.perf_counter()
    subprocess.run(
        [str(venv / "bin" / "pip"), "install", "--no-cache-dir", "--quiet"] + packages,
        check=True,
        capture_output=True,
        timeout=300,
    )
    return time.perf_counter() - start


def run_with_uv(venv: Path, packages: list[str]) -> float:
    """Install using uv, return elapsed time."""
    start = time.perf_counter()
    subprocess.run(
        ["uv", "pip", "install", "--no-cache", "--python", str(venv / "bin" / "python")] + packages,
        check=True,
        capture_output=True,
        timeout=300,
    )
    return time.perf_counter() - start


def run_with_accelero(venv: Path, packages: list[str]) -> float:
    """Install using accelero, return elapsed time."""
    start = time.perf_counter()
    subprocess.run(
        ["accelero", "--target", str(venv), "install", "--quiet"] + packages,
        check=True,
        capture_output=True,
        timeout=300,
    )
    return time.perf_counter() - start


def run_scenario(name: str, packages: list[str], tools: list[str]) -> dict:
    """Run a single scenario across tools."""
    print(f"\n{'=' * 60}")
    print(f"  {name}: {', '.join(packages)}")
    print(f"{'=' * 60}")
    results = {}

    for tool in tools:
        with tempfile.TemporaryDirectory(prefix="accelero-bench-") as tmpdir:
            venv = Path(tmpdir) / "venv"
            create_venv(venv)

            try:
                if tool == "pip":
                    elapsed = run_with_pip(venv, packages)
                elif tool == "uv":
                    elapsed = run_with_uv(venv, packages)
                elif tool == "accelero":
                    elapsed = run_with_accelero(venv, packages)
                else:
                    continue
                results[tool] = elapsed
                print(f"  {tool:>10}: {elapsed:6.2f}s")
            except Exception as e:
                print(f"  {tool:>10}: ERROR - {e}")
                results[tool] = float("inf")

    return results


def main():
    print("Holo Accelerant Benchmark")
    print("=" * 60)
    print(f"Python: {sys.version.split()[0]}")
    print(f"Platform: {sys.platform}")

    # Clear accelero cache for clean test
    cache_dir = Path.home() / ".cache" / "accelero"
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
        print(f"Cleared accelero cache: {cache_dir}")

    tools = ["pip", "uv", "accelero"]
    all_results = {}

    # Test each scenario
    for scenario, packages in SCENARIOS.items():
        all_results[scenario] = run_scenario(scenario, packages, tools)

    # Summary
    print("\n" + "=" * 60)
    print("BENCHMARK SUMMARY")
    print("=" * 60)

    for scenario, results in all_results.items():
        print(f"\n{scenario}:")
        sorted_tools = sorted(results.items(), key=lambda x: x[1])
        baseline = results.get("pip", float("inf"))

        for tool, time_taken in sorted_tools:
            if baseline != float("inf") and time_taken != float("inf"):
                speedup = baseline / time_taken
                print(f"  {tool:>10}: {time_taken:6.2f}s  ({speedup:5.2f}x vs pip)")
            else:
                print(f"  {tool:>10}: {time_taken:6.2f}s")

    # Save JSON output
    with open("benchmark_results.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nResults saved to benchmark_results.json")


if __name__ == "__main__":
    main()
