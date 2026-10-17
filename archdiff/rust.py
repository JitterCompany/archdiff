"""Rust: crates from Cargo.toml, module dependencies from `use` and paths."""

import re
from pathlib import Path

RS_PATH = re.compile(r"\b(crate|super|[a-z_][a-z0-9_]*)::((?:[a-z_][a-z0-9_]*::)*[A-Za-z_][A-Za-z0-9_]*)")
RS_USE_GROUP = re.compile(r"\buse\s+(crate|[a-z_][a-z0-9_]*)::((?:[a-z_][a-z0-9_]*::)*)\{([^;]*)\};", re.S)


def crate_roots(files):
    """Crate ident -> src dir, from every Cargo.toml [package] in the tree."""
    roots = {}
    for path, text in files.items():
        if path.endswith("Cargo.toml"):
            m = re.search(r'\[package\][^\[]*?name\s*=\s*"([^"]+)"', text, re.S)
            if m:
                parent = str(Path(path).parent)
                roots[m.group(1).replace("-", "_")] = "src" if parent == "." else parent + "/src"
    return roots


def module_of(path, roots):
    for ident, src in roots.items():
        if path.startswith(src + "/"):
            rel = path[len(src) + 1 : -3]
            return ident, src, [s for s in rel.split("/") if s not in ("mod", "lib", "main")]
    return None, None, None


def resolve(src_dir, segs, files):
    for n in range(len(segs), -1, -1):
        stem = "/".join([src_dir] + segs[:n])
        for cand in (stem + ".rs", stem + "/mod.rs"):
            if cand in files:
                return cand
        if n == 0:
            for cand in (src_dir + "/lib.rs", src_dir + "/main.rs"):
                if cand in files:
                    return cand
    return None


def deps(path, text, files, roots, libs, used):
    """File-level dependencies of one Rust file. Imports from git dependencies
    become `lib:<repo>/<package>[/<module>]` and are recorded in `used`."""
    ident, src, own = module_of(path, roots)
    if ident is None:
        return set()
    text = re.sub(r"//[^\n]*", "", text)
    paths = [(m.group(1), m.group(2).split("::")) for m in RS_PATH.finditer(text)]
    for m in RS_USE_GROUP.finditer(text):
        prefix = [s for s in m.group(2).split("::") if s]
        for item in re.findall(r"([a-z_][a-z0-9_]*)\s*(?:::|,|\}|$)", m.group(3)):
            paths.append((m.group(1), prefix + [item]))
        if m.group(1) in libs:
            for item in re.findall(r"(?<![\w:])[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*", m.group(3)):
                paths.append((m.group(1), prefix + item.split("::")))
    out = set()
    for head, segs in paths:
        if head == "crate":
            target = resolve(src, segs, files)
        elif head == "super":
            target = resolve(src, own[:-1] + segs, files)
        elif head in roots and head != ident:
            target = resolve(roots[head], segs, files)
        elif head in libs:
            lib = libs[head]
            used.add((head, "::".join(segs)))
            mod = "/" + segs[0] if len(segs) > 1 else ""
            target = f"lib:{lib['repo']}/{lib['package']}{mod}"
        else:
            continue
        if target and target != path:
            out.add(target)
    return out
