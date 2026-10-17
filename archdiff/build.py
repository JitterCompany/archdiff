"""Assemble everything the viewer needs for one base..head comparison."""

import re
from collections import defaultdict

from . import contracts, external, rust, system, ts
from .gitio import Git
from .mapping import Mapper

CODE = (".rs", ".ts", ".tsx", ".js", ".jsx", ".mjs")
TSCONFIG = re.compile(r"(^|/)tsconfig[^/]*\.json$")


def snapshot(g, rev, cfg, mapper):
    keep = lambda p: (p.endswith(CODE) and mapper(p)) or p.endswith("Cargo.toml") or bool(TSCONFIG.search(p))
    files = g.read_tree(rev, keep)
    roots = rust.crate_roots(files)
    libs = external.git_deps(files)
    alias_list = ts.aliases(files, cfg["ts"]["aliases"]) if cfg["ts"].get("tsconfig", True) else ts.aliases({}, cfg["ts"]["aliases"])
    infra = [(re.compile(i["pattern"]), "ext:" + i["node"]) for i in cfg["infra"]]
    used, edges = set(), set()
    for path, text in files.items():
        if path.endswith(".rs"):
            edges |= {(path, d) for d in rust.deps(path, text, files, roots, libs, used)}
        elif path.endswith(CODE):
            edges |= {(path, d) for d in ts.deps(path, text, files, alias_list)}
        if path.endswith(CODE):
            edges |= {(path, node) for rx, node in infra if rx.search(text)}
    clist = contracts.extract(files, cfg["contracts"]["extractors"])
    if cfg["contracts"].get("cross_system"):
        edges |= contracts.cross_system_deps(files, clist, lambda p: (mapper(p) or "").split("/")[0])
    return {"files": files, "edges": edges, "contracts": clist, "libs": libs, "used": used}


def crate_moves(base_nodes, head_nodes, lib_repos):
    """Internal crates that disappear and reappear inside a git dependency,
    matched on module names (git rename detection stops at the repo boundary)."""
    def modules(nodes, prefix):
        return {n[len(prefix) + 1 :].split("/")[0] for n in nodes if n.startswith(prefix + "/")}

    crate = lambda n: "/".join(n.split("/")[:2])
    internal = lambda n: n.split("/")[0] not in lib_repos
    gone = {crate(n) for n in base_nodes if internal(n)} - {crate(n) for n in head_nodes if internal(n)}
    new = {crate(n) for n in head_nodes - base_nodes if not internal(n)}
    out = {}
    for g in gone:
        gm = modules(base_nodes, g)
        if not gm:
            continue
        sim = lambda c: len(gm & modules(head_nodes, c)) / len(gm | modules(head_nodes, c))
        best = max(new, key=sim, default=None)
        if best and sim(best) >= 0.3:
            out[g] = best
    return out


def build(repo, base, head, cfg, title=""):
    g = Git(repo)
    mapper = Mapper(cfg)
    b, h = snapshot(g, base, cfg, mapper), snapshot(g, head, cfg, mapper)
    libs, lib_nodes, lib_changes = external.analyse(b["libs"], h["libs"], b["used"], h["used"])
    lib_repos = {lib["repo"] for lib in libs}
    in_lib = lambda lp: lp.split("/")[0] in lib_repos
    status, renames, numstat = g.changes(base, head)

    # Express the base in head paths, so a moved file keeps its identity and
    # only dependencies that really changed show up as added or removed.
    base_edges = {(renames.get(a, a), renames.get(x, x)) for a, x in b["edges"]}
    moves = defaultdict(int)
    for old, new in renames.items():
        lo, ln = mapper(old), mapper(new)
        if not lo or not ln or lo == ln:
            continue
        so, sn = lo.split("/"), ln.split("/")
        k = 0
        while k < min(len(so), len(sn)) - 1 and so[-1 - k] == sn[-1 - k]:
            k += 1
        moves[("/".join(so[: len(so) - k]), "/".join(sn[: len(sn) - k]))] += 1

    changes, other = {}, []
    for path, add, dele in numstat:
        lp = mapper(path)
        if lp is None:
            if not any(r.match(path) for r in mapper.ignore):
                other.append({"path": path, "add": add, "del": dele})
            continue
        c = changes.setdefault(lp, {"add": 0, "del": 0, "files": []})
        c["add"] += add
        c["del"] += dele
        c["files"].append({"path": path, "add": add, "del": dele, "status": status.get(path, "M")})
    listed = {f["path"] for c in changes.values() for f in c["files"]}
    for path, st in status.items():
        if st == "D" and mapper(path) and path not in listed:
            changes.setdefault(mapper(path), {"add": 0, "del": 0, "files": []})["files"].append({"path": path, "add": 0, "del": 0, "status": "D"})
    changes.update(lib_changes)

    def nodes_of(files, rn=lambda p: p):
        return {lp for p in files if not TSCONFIG.search(p) and (lp := mapper(rn(p)))}

    base_nodes = nodes_of(b["files"], lambda p: renames.get(p, p)) | lib_nodes["base"]
    head_nodes = nodes_of(h["files"]) | lib_nodes["head"]
    cmoves = crate_moves(base_nodes, head_nodes, lib_repos)

    def remap(lp):
        for old, new in cmoves.items():
            if lp == old or lp.startswith(old + "/"):
                return new + lp[len(old) :]
        return lp

    for old, new in cmoves.items():
        moves[(old, new)] += sum(1 for p in b["files"] if (mapper(p) or "").startswith(old + "/"))
    # Moved crate files with no counterpart in the dependency simply drop out.
    base_nodes = {m for n in base_nodes if not in_lib(m := remap(n)) or m in lib_nodes["base"] | lib_nodes["head"]}

    def lp_of(p):
        if p.startswith("lib:"):
            return p[4:]
        return p if p.startswith("ext:") else mapper(p)

    def snap(lp, known):
        # A `lib:` target names a guessed module; fall back to the deepest node that exists.
        while lp.count("/") > 1 and lp not in known:
            lp = lp.rsplit("/", 1)[0]
        return lp

    def agg(edges, known, rm=lambda x: x):
        out = set()
        for a, x in edges:
            la, lb = lp_of(a), lp_of(x)
            la, lb = la and rm(la), lb and rm(lb)
            if not la or not lb or in_lib(la):
                continue  # internals of a git dependency are not analysed
            if x.startswith("lib:"):
                lb = snap(lb, known)
            if not (la + "/").startswith(lb + "/") and not (lb + "/").startswith(la + "/"):
                out.add((la, lb))
        return out

    be, he = agg(base_edges, base_nodes, remap), agg(h["edges"], head_nodes)
    edges = [{"from": a, "to": x, "s": "same"} for a, x in be & he]
    edges += [{"from": a, "to": x, "s": "add"} for a, x in he - be]
    edges += [{"from": a, "to": x, "s": "del"} for a, x in be - he]

    bc = {(k, n): p for k, n, p in b["contracts"]}
    hc = {(k, n): p for k, n, p in h["contracts"]}
    contract_changes = [{"kind": k, "name": n, "s": "add", "node": mapper(hc[(k, n)])} for k, n in sorted(set(hc) - set(bc))]
    contract_changes += [{"kind": k, "name": n, "s": "del", "node": mapper(bc[(k, n)])} for k, n in sorted(set(bc) - set(hc))]

    labels = dict(cfg["labels"])
    labels |= {"ext:" + i["node"]: i.get("label", i["node"]) for i in cfg["infra"]}
    labels |= {lib["repo"]: f"{lib['repo']} (git dep)" for lib in libs}
    return {
        "title": title,
        "base": g.run("rev-parse", "--short", base).strip(),
        "head": g.run("rev-parse", "--short", head).strip(),
        "systems": labels,
        "libs": libs,
        "system": system.view(b["files"], h["files"], cfg["system_view"], renames, mapper),
        "nodes": sorted(base_nodes | head_nodes | set(changes)),
        "file_names": {mapper(p): p.rsplit("/", 1)[-1] for p in {**b["files"], **h["files"]} if p.endswith(".rs") and mapper(p)},
        "added": sorted(head_nodes - base_nodes),
        "removed": sorted(base_nodes - head_nodes),
        "moved": sorted({mapper(n) for n in renames.values() if mapper(n)}),
        "moves": [{"from": a, "to": x, "files": n} for (a, x), n in sorted(moves.items(), key=lambda m: -m[1])],
        "changes": changes,
        "other": other,
        "edges": edges,
        "contracts": contract_changes,
    }
