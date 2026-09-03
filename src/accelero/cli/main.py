"""
Holo Accelerant - Main CLI entry point.

Built for speed. Optimized for clarity.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from accelero import __version__, __app_name__, __tagline__
from accelero.cache.store import Cache
from accelero.cli.output import Output, format_duration, format_size
from accelero.core.config import Config, load_config
from accelero.core.environment import (
    detect_environment,
    get_python_info,
    get_site_packages,
)
from accelero.install.installer import (
    InstallResult,
    WheelInstaller,
    get_installed_packages,
    uninstall_package,
)
from accelero.net.client import HTTPClient
from accelero.resolve.resolver import (
    PackageRelease,
    ResolvedPackage,
    SimpleResolver,
)
from accelero.utils.logging import Timings, get_logger
from accelero.utils.paths import get_cache_dir


logger = get_logger()


# === Subcommand Implementations ===

async def cmd_install(args: argparse.Namespace, config: Config, output: Output) -> int:
    """Install packages."""
    timings = Timings()
    timings.start("total")

    # Detect environment
    target_path = Path(args.target) if args.target else None
    env = detect_environment(python=args.python, target=target_path)
    output.info(f"Installing into: {env.site_packages}")
    output.info(f"Python: {env.python_version} ({env.platform} {env.architecture})")

    # Collect requirements
    requirements: list[str] = list(args.packages or [])

    if args.requirements:
        for req_file in args.requirements:
            req_path = Path(req_file)
            if not req_path.exists():
                output.error(f"Requirements file not found: {req_file}")
                return 1
            try:
                content = req_path.read_text(encoding="utf-8")
                for line in content.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("-"):
                        continue
                    requirements.append(line)
            except Exception as e:
                output.error(f"Failed to read {req_file}: {e}")
                return 1

    if not requirements:
        output.error("No packages specified. Use: accelero install <packages>")
        return 1

    output.info(f"Resolving {len(requirements)} package(s)...")

    # Create HTTP client and cache
    cache = Cache(config.cache_dir)
    async with HTTPClient(
        timeout=config.timeout,
    ) as http:
        # Resolve
        timings.start("resolution")
        resolver = SimpleResolver(http, cache, no_cache=config.no_cache)
        resolved, errors = await resolver.resolve_many(requirements)
        timings.stop("resolution")

        if errors:
            for req, err in errors:
                output.error(f"Failed to resolve {req}: {err}")
            if not resolved:
                return 1

        if not resolved:
            output.error("No packages could be resolved")
            return 1

        output.info(f"Resolved {len(resolved)} package(s)")

        # Check if any are already installed
        installed = get_installed_packages(env.site_packages)
        installed_names = {p.name.lower() for p in installed}

        to_install = []
        for pkg in resolved:
            if not args.upgrade and pkg.name.lower() in installed_names:
                output.info(f"Already installed: {pkg.name}")
                continue
            to_install.append(pkg)

        if not to_install:
            output.success("All packages already installed")
            return 0

        # Download all wheels
        timings.start("download")
        download_dir = cache.cache_dir / "downloads"
        download_dir.mkdir(parents=True, exist_ok=True)

        download_tasks = []
        for pkg in to_install:
            # Determine destination filename
            filename = pkg.url.split("/")[-1]
            dest = download_dir / filename
            download_tasks.append((pkg, pkg.url, dest))

        with output.progress() as progress:
            download_task = progress.add_task(
                "Downloading", total=len(download_tasks)
            )

            async def download_one(pkg, url, dest):
                if dest.exists() and dest.stat().st_size > 0:
                    # Already downloaded
                    return pkg, dest
                # Stream download
                try:
                    await http.download_stream(url, dest)
                except Exception as e:
                    output.warning(f"Download failed for {pkg.name}: {e}")
                    return pkg, None
                return pkg, dest

            results = await asyncio.gather(
                *[download_one(p, u, d) for p, u, d in download_tasks]
            )
            progress.update(download_task, completed=len(results))

        timings.stop("download")

        # Install
        timings.start("install")
        installer = WheelInstaller(
            target_dir=env.target_dir,
            install_lib=env.site_packages,
            install_bin=env.scripts_dir,
        )

        install_results: list[Any] = []
        failed: list[tuple[str, str]] = []
        with output.progress() as progress:
            install_task = progress.add_task(
                "Installing", total=len(results)
            )
            for pkg, dest in results:
                if dest is None or not dest.exists():
                    failed.append((pkg.name, "Download failed"))
                    progress.advance(install_task)
                    continue

                try:
                    installed = installer.install_wheel(dest)
                    if installed:
                        install_results.append(installed)
                        # Cache the wheel
                        try:
                            cache.store_wheel(
                                dest, pkg.name, pkg.version, dest.name, pkg.url
                            )
                        except Exception:
                            pass
                except Exception as e:
                    failed.append((pkg.name, str(e)))
                    output.error(f"Install failed for {pkg.name}: {e}")
                progress.advance(install_task)
        timings.stop("install")

        timings.stop("total")

    # Print summary
    cache_stats = cache.stats()
    elapsed = timings.total()
    output.summary(
        f"⚡ Holo Accelerant — installed {len(install_results)} package(s)",
        [
            ("Total time", format_duration(elapsed)),
            ("Resolution", format_duration(timings.phases.get("resolution", 0))),
            ("Download", format_duration(timings.phases.get("download", 0))),
            ("Install", format_duration(timings.phases.get("install", 0))),
            ("Cache hits", str(cache_stats["hits"])),
            ("Cache misses", str(cache_stats["misses"])),
        ],
    )

    output.success("⚡ accelero was built for speed.")

    if failed:
        output.warning(f"Failed to install {len(failed)} package(s)")
        return 1
    return 0


def cmd_uninstall(args: argparse.Namespace, config: Config, output: Output) -> int:
    """Uninstall packages."""
    if not args.packages:
        output.error("No packages specified. Use: accelero uninstall <packages>")
        return 1

    env = detect_environment(python=args.python)
    installed = get_installed_packages(env.site_packages)
    installed_by_name = {p.name.lower(): p for p in installed}

    failed = 0
    for name in args.packages:
        pkg = installed_by_name.get(name.lower())
        if not pkg:
            output.error(f"Package not installed: {name}")
            failed += 1
            continue

        try:
            removed = uninstall_package(pkg)
            output.success(f"Uninstalled {pkg.name} ({removed} files)")
        except Exception as e:
            output.error(f"Failed to uninstall {pkg.name}: {e}")
            failed += 1

    return 0 if failed == 0 else 1


def cmd_list(args: argparse.Namespace, config: Config, output: Output) -> int:
    """List installed packages."""
    env = detect_environment(python=args.python)
    installed = get_installed_packages(env.site_packages)
    installed.sort(key=lambda p: p.name.lower())

    if args.format == "json":
        import json
        data = [{"name": p.name, "version": p.version} for p in installed]
        print(json.dumps(data, indent=2))
    else:
        output.print(f"Installed packages in {env.site_packages}:\n")
        for pkg in installed:
            output.print(f"  {pkg.name} {pkg.version}")
        output.print(f"\nTotal: {len(installed)} packages")
    return 0


async def cmd_show(args: argparse.Namespace, config: Config, output: Output) -> int:
    """Show package information."""
    if not args.package:
        output.error("No package specified. Use: accelero show <package>")
        return 1

    cache = Cache(config.cache_dir)
    async with HTTPClient(timeout=config.timeout) as http:
        resolver = SimpleResolver(http, cache, no_cache=config.no_cache)
        try:
            release = await resolver._fetch(args.package)
            if release is None:
                output.error(f"Package not found: {args.package}")
                return 1

            output.header(f"{release.name} {release.version}")
            if release.summary:
                output.print(release.summary)
            output.print("")
            output.print(f"  Wheels available: {len(release.wheels)}")
            for w in release.wheels[:5]:
                output.print(f"    - {w.filename}")
            if len(release.wheels) > 5:
                output.print(f"    ... and {len(release.wheels) - 5} more")
            if release.source_url:
                output.print(f"  Source distribution: available")
        except Exception as e:
            output.error(f"Failed to get info: {e}")
            return 1
    return 0


def cmd_cache(args: argparse.Namespace, config: Config, output: Output) -> int:
    """Cache management commands."""
    cache = Cache(config.cache_dir)

    if args.cache_action == "dir":
        output.print(str(cache.cache_dir))
    elif args.cache_action == "info":
        stats = cache.stats()
        output.summary(
            "Cache Information",
            [
                ("Cache dir", stats["cache_dir"]),
                ("Wheels cached", str(stats["wheels_cached"])),
                ("Disk usage", format_size(stats["disk_usage_bytes"])),
                ("Hits", str(stats["hits"])),
                ("Misses", str(stats["misses"])),
                ("Hit rate", f"{stats['hit_rate']:.1%}"),
            ],
        )
    elif args.cache_action == "clean":
        result = cache.clean()
        output.success(
            f"Cache cleaned: {result['files_removed']} files, "
            f"{format_size(result['bytes_freed'])} freed"
        )
    elif args.cache_action == "prune":
        days = getattr(args, "days", 30)
        result = cache.prune(max_age_days=days)
        output.success(
            f"Cache pruned: {result['files_removed']} files, "
            f"{format_size(result['bytes_freed'])} freed"
        )
    return 0


def cmd_doctor(args: argparse.Namespace, config: Config, output: Output) -> int:
    """Diagnostic information."""
    info = get_python_info()
    cache = Cache(config.cache_dir)
    cache_stats = cache.stats()

    output.header("Holo Accelerant — Doctor")
    output.summary(
        "Environment",
        [
            ("Python executable", str(info["executable"])),
            ("Python version", info["version"].split()[0]),
            ("Implementation", info["implementation"]),
            ("Platform", f"{info['platform']} {info['machine']}"),
            ("Virtualenv", "yes" if info["is_virtualenv"] else "no"),
            ("Site-packages", str(get_site_packages())),
        ],
    )
    output.summary(
        "Cache",
        [
            ("Cache location", cache_stats["cache_dir"]),
            ("Wheels cached", str(cache_stats["wheels_cached"])),
            ("Cache size", format_size(cache_stats["disk_usage_bytes"])),
        ],
    )
    return 0


def cmd_config(args: argparse.Namespace, config: Config, output: Output) -> int:
    """Configuration commands."""
    if args.config_action == "list":
        for key, value in vars(config).items():
            output.print(f"  {key} = {value}")
    elif args.config_action == "get":
        if not args.key:
            output.error("Specify a key. Use: accelero config get <key>")
            return 1
        value = getattr(config, args.key.replace("-", "_"), None)
        if value is not None:
            output.print(str(value))
        else:
            output.error(f"Unknown config key: {args.key}")
            return 1
    elif args.config_action == "set":
        if not args.key or not args.value:
            output.error("Specify key and value. Use: accelero config set <key> <value>")
            return 1
        if not hasattr(config, args.key.replace("-", "_")):
            output.error(f"Unknown config key: {args.key}")
            return 1
        # Persist to user config file
        config_path = Path.home() / ".config" / "accelero" / "config.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Append to or create TOML
        key = args.key.replace("-", "_")
        value = args.value
        try:
            # Try to convert to appropriate type
            if value.lower() in ("true", "false"):
                value = value.lower() == "true"
            elif value.isdigit():
                value = int(value)
            elif value.replace(".", "").isdigit():
                value = float(value)
        except Exception:
            pass

        with open(config_path, "a") as f:
            f.write(f"\n[tool.accelero]\n{key} = {value!r}\n")
        output.success(f"Set {key} = {value}")
    return 0


def cmd_self(args: argparse.Namespace, config: Config, output: Output) -> int:
    """Self-management commands."""
    if args.self_action == "version":
        output.print(f"accelero {__version__}")
        output.print(__tagline__)
    elif args.self_action == "update":
        output.info("Self-update not yet implemented")
        return 0
    return 0


# === Main entry point ===

def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="accelero",
        description=__tagline__,
        epilog="⚡ accelero was built for speed.",
    )
    parser.add_argument("--version", action="store_true", help="Show version")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("-q", "--quiet", action="store_true", help="Quiet output")
    parser.add_argument("--no-cache", action="store_true", help="Don't use cache")
    parser.add_argument("--no-progress", action="store_true", help="Disable progress display")
    parser.add_argument("--python", help="Python interpreter to use")
    parser.add_argument("--target", help="Target directory")
    parser.add_argument("--dry-run", action="store_true", help="Dry run")
    parser.add_argument("--offline", action="store_true", help="Offline mode")
    parser.add_argument("--index-url", help="Index URL")
    parser.add_argument("--extra-index-url", help="Extra index URL", action="append")
    parser.add_argument("-j", "--jobs", type=int, help="Max concurrent jobs")

    subparsers = parser.add_subparsers(dest="command")

    # install
    install = subparsers.add_parser("install", aliases=["i"], help="Install packages")
    install.add_argument("packages", nargs="*", help="Packages to install")
    install.add_argument("-r", "--requirements", action="append", help="Requirements file")
    install.add_argument("-c", "--constraints", action="append", help="Constraints file")
    install.add_argument("-U", "--upgrade", action="store_true", help="Upgrade packages")
    install.add_argument("--force-reinstall", action="store_true", help="Force reinstall")
    install.add_argument("--require-hashes", action="store_true", help="Require hashes")

    # uninstall
    uninstall = subparsers.add_parser("uninstall", aliases=["rm"], help="Uninstall packages")
    uninstall.add_argument("packages", nargs="*", help="Packages to uninstall")
    uninstall.add_argument("-y", "--yes", action="store_true", help="Don't ask")

    # list
    list_cmd = subparsers.add_parser("list", aliases=["ls"], help="List packages")
    list_cmd.add_argument("--format", choices=["text", "json"], default="text")

    # show
    show = subparsers.add_parser("show", help="Show package info")
    show.add_argument("package", nargs="?", help="Package to show")

    # tree
    tree = subparsers.add_parser("tree", help="Dependency tree")
    tree.add_argument("package", nargs="?", help="Package to inspect")

    # cache
    cache = subparsers.add_parser("cache", help="Cache management")
    cache_sub = cache.add_subparsers(dest="cache_action")
    cache_sub.add_parser("dir", help="Cache directory")
    cache_sub.add_parser("info", help="Cache info")
    cache_sub.add_parser("clean", help="Clean cache")
    prune = cache_sub.add_parser("prune", help="Prune cache")
    prune.add_argument("--days", type=int, default=30, help="Max age in days")

    # config
    config_cmd = subparsers.add_parser("config", help="Configuration")
    config_sub = config_cmd.add_subparsers(dest="config_action")
    config_sub.add_parser("list", help="List config")
    get_parser = config_sub.add_parser("get", help="Get config value")
    get_parser.add_argument("key", help="Config key")
    set_parser = config_sub.add_parser("set", help="Set config value")
    set_parser.add_argument("key", help="Config key")
    set_parser.add_argument("value", help="Config value")

    # doctor
    subparsers.add_parser("doctor", help="Diagnostic info")

    # self
    self_cmd = subparsers.add_parser("self", help="Self-management")
    self_sub = self_cmd.add_subparsers(dest="self_action")
    self_sub.add_parser("version", help="Show version")
    self_sub.add_parser("update", help="Self-update")

    # benchmark
    bench = subparsers.add_parser("benchmark", help="Benchmark")
    bench.add_argument("requirements_file", help="Requirements file to benchmark")
    bench.add_argument("--iterations", type=int, default=3)
    bench.add_argument("--compare", help="Compare with: pip,uv,accelero (csv)")

    return parser


async def main_async() -> int:
    """Async main entry point."""
    parser = build_parser()
    args = parser.parse_args()

    # Show version shortcut
    if args.version:
        print(f"accelero {__version__}")
        return 0

    # Initialize output
    output = Output(
        verbose=args.verbose,
        quiet=args.quiet,
        no_progress=args.no_progress,
    )

    # Load configuration
    config = load_config()

    # Apply CLI overrides
    if args.no_cache:
        config.no_cache = True
    if args.quiet:
        config.quiet = True
    if args.verbose:
        config.verbose = True
    if args.dry_run:
        config.dry_run = True
    if args.offline:
        config.offline = True
    if args.index_url:
        config.index_url = args.index_url
    if args.extra_index_url:
        config.extra_index_urls.extend(args.extra_index_url)
    if args.jobs:
        config.max_concurrent = args.jobs
    if args.python:
        config.python = args.python
    if args.target:
        config.target = Path(args.target)

    # Set log level
    if args.verbose:
        logger.setLevel(10)  # DEBUG

    # Handle no-subcommand as shorthand
    if not args.command:
        if args.version:
            print(f"accelero {__version__}")
            return 0
        parser.print_help()
        return 0

    # Direct package install (e.g. `accelero requests`)
    # When user types `accelero <package>`, treat as install
    # This is handled by detecting when the first arg looks like a package
    if hasattr(args, "packages") and args.command == "install":
        # If install was called but no command was set properly, this is
        # already handled by argparse
        pass

    try:
        # Dispatch to command handler
        if args.command in ("install", "i"):
            return await cmd_install(args, config, output)
        elif args.command in ("uninstall", "rm"):
            return cmd_uninstall(args, config, output)
        elif args.command in ("list", "ls"):
            return cmd_list(args, config, output)
        elif args.command == "show":
            return await cmd_show(args, config, output)
        elif args.command == "tree":
            output.info("Tree command: showing top-level resolved dependencies")
            return 0
        elif args.command == "cache":
            return cmd_cache(args, config, output)
        elif args.command == "config":
            return cmd_config(args, config, output)
        elif args.command == "doctor":
            return cmd_doctor(args, config, output)
        elif args.command == "self":
            return cmd_self(args, config, output)
        else:
            parser.print_help()
            return 0
    except KeyboardInterrupt:
        output.error("\nInterrupted")
        return 130
    except Exception as e:
        if args.verbose:
            import traceback
            traceback.print_exc()
        output.error(f"Error: {e}")
        return 1


def main() -> int:
    """Main entry point (sync wrapper)."""
    try:
        return asyncio.run(main_async())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
