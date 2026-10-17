"""Runtime view: tasks, the connections between them and the message types on
each connection, extracted per app from the project's wiring code."""

from ..mapping import glob_re
from . import rtic, tokio
from .common import attach_types, diff_app, pair_apps

FRONTENDS = {"tokio": tokio, "rtic": rtic}


def apps(files, cfg):
    fe = FRONTENDS.get(cfg.get("kind"))
    if not fe:
        return []
    globs = [glob_re(g) for g in cfg.get("entry", ["**/main.rs"])]
    entries = [p for p in files if any(g.match(p) for g in globs) and fe.score(files[p]) > 2]
    out = []
    for entry in sorted(entries):
        app = fe.snapshot(files, entry, cfg)
        attach_types(app, files)
        out.append(app)
    return out


def view(base_files, head_files, cfg, renames, mapper):
    if not cfg.get("kind"):
        return None
    pairs = pair_apps(apps(base_files, cfg), apps(head_files, cfg))
    return {"kind": cfg["kind"], "apps": [diff_app(b, h, renames, mapper) for b, h in pairs]} if pairs else None
