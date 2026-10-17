"""Project configuration (archdiff.toml).

Everything that is specific to one repository lives in its config file: how
paths group into systems, which infrastructure and contracts to look for, and
how the runtime wiring is written. The tool itself stays project-agnostic.
"""

import tomllib
from pathlib import Path

DEFAULTS = {
    "ignore": ["**/node_modules/**", "**/target/**", "**/dist/**", "**/test_output/**", "**/*.lock", "**/package-lock.json"],
    # Without a matching [[map]] rule, files with these suffixes map to their path with `src/` dropped.
    "default_map": True,
    "source": [".rs", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".html", ".scss", ".css", "Cargo.toml", "package.json"],
    "map": [],
    "labels": {},
    "ts": {"aliases": {}, "tsconfig": True},
    "infra": [],
    "contracts": {"extractors": [], "cross_system": False},
    "system_view": {},
}


def load(path):
    cfg = {k: (v.copy() if isinstance(v, (dict, list)) else v) for k, v in DEFAULTS.items()}
    if path and Path(path).exists():
        user = tomllib.loads(Path(path).read_text())
        for key, value in user.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key] = cfg[key] | value
            else:
                cfg[key] = value
    return cfg
