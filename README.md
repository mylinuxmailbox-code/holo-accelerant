# Holo Accelerant

**Python package installation at ludicrous speed.**

```
accelero install flask
```

Holo Accelerant is a drop-in, high-performance alternative to `pip`, built for speed and correctness. It features parallel downloads, intelligent caching, concurrent resolution, and optimized I/O.

## Installation

```bash
# Install via pip
pip install holo-accelerant

# Or install in development mode
git clone https://github.com/holo-accelerant/holo-accelerant.git
cd holo-accelerant
pip install -e .
```

After installation, use the `accelero` command:

```bash
accelero --version
```

## Quick Start

### Install packages

```bash
# Install a single package
accelero install requests

# Install multiple packages
accelero install numpy pandas torch

# Install from requirements file
accelero install -r requirements.txt

# Upgrade a package
accelero install --upgrade requests
```

### Uninstall packages

```bash
accelero uninstall requests
```

### List installed packages

```bash
accelero list
accelero list --format=json
```

### Show package information

```bash
accelero show requests
```

### Cache management

```bash
accelero cache dir     # Show cache location
accelero cache info    # Show cache statistics
accelero cache clean   # Clear the cache
accelero cache prune   # Prune old entries
```

### Diagnostics

```bash
accelero doctor         # Environment and configuration report
```

### Configuration

```bash
accelero config list    # Show all configuration
accelero config get index-url
accelero config set max-concurrent 64
```

## CLI Reference

### Global Options

| Option | Description |
|--------|-------------|
| `-v, --verbose` | Verbose output |
| `-q, --quiet` | Quiet output (errors only) |
| `--no-cache` | Don't use local cache |
| `--no-progress` | Disable progress display |
| `--python PYTHON` | Python interpreter to use |
| `--target DIR` | Install into target directory |
| `--index-url URL` | Package index URL |
| `--offline` | Offline mode (use cache only) |
| `-j, --jobs N` | Max concurrent downloads |

### Commands

#### `install` (alias: `i`)
```bash
accelero install [OPTIONS] PACKAGES...
  -r, --requirements FILE    Requirements file
  -U, --upgrade              Upgrade packages
  --require-hashes           Require hash verification
```

#### `uninstall` (alias: `rm`)
```bash
accelero uninstall PACKAGES...
```

#### `list` (alias: `ls`)
```bash
accelero list [--format text|json]
```

#### `show`
```bash
accelero show PACKAGE
```

#### `tree`
```bash
accelero tree [PACKAGE]
```

#### `cache`
```bash
accelero cache [dir|info|clean|prune]
```

#### `config`
```bash
accelero config [list|get KEY|set KEY VALUE]
```

#### `doctor`
```bash
accelero doctor
```

#### `self`
```bash
accelero self version   # Show version
accelero self update    # Self-update
```

## Performance Philosophy

Holo Accelerant optimizes the entire installation pipeline:

### Resolution
- **Parallel metadata fetching**: Up to 20 concurrent package index lookups
- **Intelligent caching**: All metadata responses cached with TTL
- **Marker evaluation**: Early pruning of platform-incompatible versions
- **BFS traversal**: Full transitive dependency resolution

### Download
- **HTTP/2 connection pooling**: Multiplexed requests on single connections
- **Concurrent downloads**: Up to 32 simultaneous file downloads
- **Keep-alive**: Reuse connections across requests
- **Streaming I/O**: Direct disk writes with progress tracking

### Installation
- **Parallel file extraction**: Thread pool for wheel file unpacking
- **Atomic writes**: Temp file + rename pattern
- **Lazy `.pyc` compilation**: Only compile what's needed
- **Content-addressable cache**: Deduplicate identical wheels

## Benchmark Methodology

Benchmarks compare installation of identical packages in fresh virtual environments. All times are wall-clock time from venv creation to installation complete.

### Results (Sample - 4 packages: requests, click, pyyaml, six)

| Tool | Cold Install | Warm Install |
|------|-------------|--------------|
| pip | ~5-12s | ~3-5s |
| uv | ~1.5-4s | ~0.3-1s |
| accelero | ~2-5s | ~1-2s |

Results vary based on network conditions, package size, and system configuration.

### What affects performance

- **Network latency**: First-time downloads dominate install time
- **Cache state**: Warm installs with cached metadata are significantly faster
- **Package size**: Large wheels (numpy, torch) benefit more from parallel downloads
- **Dependency depth**: Packages with many transitive deps take longer to resolve

## pip Compatibility

Holo Accelerant aims to be a transparent drop-in replacement for pip.

### Supported
- PyPI and PEP 503 compatible indexes
- PEP 691 JSON API
- Wheels (pure Python and platform-specific)
- Source distributions (tar.gz)
- `requirements.txt` files
- Constraints files
- Environment markers (platform, python_version, etc.)
- Extras (`requests[security]`)
- Version specifiers (`>=2.0`, `==1.5`, `!=2.0`, etc.)
- Private package indexes
- HTTP/HTTPS authentication
- Virtual environment detection

### Known Limitations
- Editable installs (`pip install -e .`) not yet supported
- Build isolation for sdists uses `pip install` fallback
- Hash verification (`--require-hashes`) is verified post-download, not enforced per-request

## Cache Behavior

The cache is stored at:
- Linux: `~/.cache/accelero/`
- macOS: `~/Library/Caches/accelero/`
- Windows: `%LOCALAPPDATA%\accelero\cache\`

### Structure
```
cache/
├── wheels/          # Content-addressed wheel files
├── sdists/          # Content-addressed source distributions
├── metadata/        # Package metadata JSON
├── index/           # Cached package index data
└── cache.db         # SQLite metadata database
```

### TTL
- Package metadata: 10 minutes (configurable)
- Index data: 1 hour (configurable)
- Wheels/sdists: No expiration

## Configuration

### Precedence (highest to lowest)
1. CLI arguments
2. Environment variables
3. Project configuration (`pyproject.toml`)
4. User configuration (`~/.config/accelero/config.toml`)
5. Defaults

### Environment Variables
| Variable | Effect |
|----------|--------|
| `PIP_INDEX_URL` | Package index URL |
| `PIP_EXTRA_INDEX_URL` | Extra indexes (space-separated) |
| `PIP_CACHE_DIR` | Cache directory |
| `ACCELERO_TIMEOUT` | HTTP timeout in seconds |
| `ACCELERO_OFFLINE` | Enable offline mode |
| `ACCELERO_NO_CACHE` | Disable caching |

### Config File (`~/.config/accelero/config.toml`)
```toml
[tool.accelero]
index-url = "https://pypi.org/simple/"
extra-index-urls = []
max-concurrent = 32
cache-ttl = 3600
no-cache = false
```

## Architecture

```
accelero/
├── cli/          # Command-line interface
│   ├── main.py   # CLI entry point, argument parsing
│   └── output.py # Progress display, Rich output
├── core/
│   ├── config.py # Configuration management
│   └── environment.py # Environment detection
├── net/
│   └── client.py # HTTP client with connection pooling
├── cache/
│   └── store.py  # Content-addressable cache
├── resolve/
│   └── resolver.py # Dependency resolver
├── install/
│   └── installer.py # Wheel installation engine
└── utils/
    ├── paths.py   # Path utilities
    └── logging.py # Timings and logging
```

## Development

```bash
# Clone and install
git clone https://github.com/holo-accelerant/holo-accelerant.git
cd holo-accelerant
pip install -e ".[test,dev]"

# Run tests
pytest tests/ -v

# Run benchmarks
python benchmarks/quick_bench.py
```

## Security

- All HTTP responses validated before use
- SHA256 hash verification for downloaded packages
- Credentials never logged or exposed
- Secure temporary file handling with atomic operations
- TLS certificate verification enabled by default

## Contributing

Contributions welcome. Please see the project issues for open tasks.

## License

MIT License
