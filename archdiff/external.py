"""Git dependencies declared in Cargo.toml.

Reads the dependency repos from cargo's own bare clones in ~/.cargo/git/db,
so a bump can be diffed without network access. In CI, `cargo fetch` fills
the same cache; a base rev that is missing there is fetched into it.
"""

import os
import re
import subprocess
import tomllib
from pathlib import Path

from .gitio import Git
from .mapping import collapse

CARGO_DB = Path.home() / ".cargo" / "git" / "db"
# Our own bare clones, for machines without a cargo cache (e.g. a clean CI runner).
CLONE_CACHE = Path(os.environ.get("ARCHDIFF_DEP_CACHE", Path.home() / ".cache" / "archdiff" / "deps"))
DEF_KEYWORDS = r"(?:fn|struct|enum|trait|type|const|static|mod|macro_rules!)"


def git_deps(files):
    """{crate ident: {repo, package, url, rev}} for git dependencies."""
    out = {}
    for path, text in files.items():
        if not path.endswith("Cargo.toml"):
            continue
        try:
            doc = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            continue
        for table in (doc.get("workspace", {}).get("dependencies", {}), doc.get("dependencies", {})):
            for key, spec in table.items():
                if not isinstance(spec, dict) or "git" not in spec:
                    continue
                out[key.replace("-", "_")] = {
                    "repo": re.sub(r"\.git$", "", spec["git"].rstrip("/").split("/")[-1]),
                    "package": spec.get("package", key),
                    "url": spec["git"],
                    "rev": spec.get("rev") or spec.get("tag") or spec.get("branch") or "HEAD",
                }
    return out


def find_db(repo, url, revs):
    """Bare clone that has all of revs: cargo's cache first, else our own clone.
    Missing revs are fetched once."""
    own = CLONE_CACHE / repo.lower()
    if not own.exists() and not list(CARGO_DB.glob(repo.lower() + "-*")):
        own.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--quiet", "--bare", url, str(own)], capture_output=True)
    dbs = sorted(CARGO_DB.glob(repo.lower() + "-*")) + ([own] if own.exists() else [])
    for db in dbs:
        g = Git(None, git_dir=db)
        missing = [r for r in revs if not g.ok("cat-file", "-e", f"{r}^{{commit}}")]
        if missing and g.ok("fetch", "--quiet", url, *missing):
            missing = [r for r in missing if not g.ok("cat-file", "-e", f"{r}^{{commit}}")]
        if not missing:
            return g
    return None


def crate_dirs(files):
    dirs = {}
    for path, text in files.items():
        if path.endswith("Cargo.toml"):
            m = re.search(r'\[package\][^\[]*?name\s*=\s*"([^"]+)"', text, re.S)
            if m:
                parent = str(Path(path).parent)
                dirs[m.group(1)] = "src" if parent == "." else parent + "/src"
    return dirs


def module_path(repo, package, src, path):
    """Dependency modules stop at the first level below the crate; deeper
    structure is the dependency's own business (and can be huge, e.g. a PAC)."""
    if not path.startswith(src + "/") or not path.endswith(".rs"):
        return None
    return "/".join(collapse(f"{repo}/{package}/{path[len(src) + 1 :]}").split("/")[:3])


def _changed_lines(g, base, head, src_dirs, sides="+-"):
    diff = g.run("diff", "-U0", base, head, "--", *src_dirs)
    return "\n".join(l[1:] for l in diff.splitlines() if l[:1] in sides and not l.startswith(("+++", "---")))


def bump_impact(g, base, head, src_dirs, used_items):
    """Per imported item: `definition` when a changed line declares that name,
    `mentioned` when it only appears in a changed line."""
    changed = _changed_lines(g, base, head, src_dirs)
    touched = []
    for item in sorted(used_items):
        name = item.split("::")[-1]
        if name in ("self", "*") or len(name) < 3:
            continue
        if re.search(rf"\b{DEF_KEYWORDS}\s+{re.escape(name)}\b|\bpub\s+use\b[^\n]*\b{re.escape(name)}\b", changed):
            touched.append({"item": item, "how": "definition"})
        elif re.search(rf"\b{re.escape(name)}\b", changed):
            touched.append({"item": item, "how": "mentioned"})
    return touched


def new_definitions(g, base, head, src_dirs, items):
    """Items we started using that the bump itself introduces."""
    added = _changed_lines(g, base, head, src_dirs, sides="+")
    return sorted(i for i in items if re.search(rf"\b{DEF_KEYWORDS}\s+{re.escape(i.split('::')[-1])}\b", added))


def analyse(base_libs, head_libs, base_used, head_used):
    """Per dependency repo: rev change, module nodes, churn and bump impact."""
    repos = {}
    for side, libs in (("base", base_libs), ("head", head_libs)):
        for ident, lib in libs.items():
            r = repos.setdefault(lib["repo"], {"repo": lib["repo"], "url": lib["url"], "base": None, "head": None, "packages": {}})
            r[side] = lib["rev"]
            r["packages"].setdefault(lib["package"], set()).add(ident)
    nodes, changes, out = {"base": set(), "head": set()}, {}, []
    keep = lambda p: p.endswith((".rs", "Cargo.toml"))
    for r in repos.values():
        info = {"repo": r["repo"], "url": r["url"], "base": r["base"], "head": r["head"], "packages": sorted(r["packages"]),
                "touched": [], "new_in_bump": [], "usage_added": [], "usage_removed": []}
        out.append(info)
        idents = set().union(*r["packages"].values())
        bu = {f"{i}::{p}" for i, p in base_used if i in idents}
        hu = {f"{i}::{p}" for i, p in head_used if i in idents}
        info.update(used=len(hu), usage_added=sorted(hu - bu), usage_removed=sorted(bu - hu))
        g = find_db(r["repo"], r["url"], [v for v in (r["base"], r["head"]) if v])
        info["available"] = bool(g)
        if not g:
            continue
        trees = {}
        for side in ("base", "head"):
            if r[side]:
                files = g.read_tree(r[side], keep)
                trees[side] = (files, crate_dirs(files))
        for side, (files, dirs) in trees.items():
            for pkg in r["packages"]:
                if pkg in dirs:
                    nodes[side] |= {m for p in files if (m := module_path(r["repo"], pkg, dirs[pkg], p))}
                    nodes[side].add(f"{r['repo']}/{pkg}")
        if r["base"] and r["head"] and r["base"] != r["head"]:
            dirs = trees["head"][1]
            src_dirs = [dirs[p] for p in r["packages"] if p in dirs]
            for line in g.run("diff", "--numstat", r["base"], r["head"], "--", *src_dirs).splitlines():
                add, dele, path = line.split("\t")
                pkg = next((p for p in r["packages"] if p in dirs and path.startswith(dirs[p] + "/")), None)
                lp = pkg and module_path(r["repo"], pkg, dirs[pkg], path)
                if not lp:
                    continue
                add, dele = int(add) if add != "-" else 0, int(dele) if dele != "-" else 0
                c = changes.setdefault(lp, {"add": 0, "del": 0, "files": []})
                c["add"] += add
                c["del"] += dele
                c["files"].append({"path": f"{r['repo']}:{path}", "add": add, "del": dele, "status": "M"})
            common = {u.split("::", 1)[1] for u in bu & hu}
            info["touched"] = bump_impact(g, r["base"], r["head"], src_dirs, common)
            info["new_in_bump"] = new_definitions(g, r["base"], r["head"], src_dirs, {u.split("::", 1)[1] for u in hu - bu})
            info["files"] = sum(len(c["files"]) for k, c in changes.items() if k.startswith(r["repo"] + "/"))
    return out, nodes, changes
