"""Contract surface: the interfaces a system offers to other systems.

Extractors are opt-in per project via `[contracts] extractors = [...]`.
"""

import re
from collections import defaultdict

RS_ROUTE = re.compile(r'#\[(get|post|put|delete|patch)\(\s*"([^"]+)"')
RS_SCOPE = re.compile(r'web::scope\(\s*"([^"]+)"')
EXPRESS_ROUTE = re.compile(r'\b(?:app|router)\.(get|post|put|delete|patch)\(\s*["\']([^"\']+)')
METEOR_METHODS = re.compile(r"Meteor\.methods\(\s*\{")
METEOR_PUBLISH = re.compile(r"Meteor\.publish(?:Composite)?\(\s*['\"]([^'\"]+)")
METEOR_CALL = re.compile(r"""(?:Meteor|MeteorObservable)\.(?:call|callAsync|apply|applyAsync|subscribe)\(\s*['"]([^'"]+)""")
MONGO_COLL_RS = re.compile(r'\.collection(?:::<[^>]*>)?\(\s*"([^"]+)"')
MONGO_COLL_TS = re.compile(r"new\s+Mongo\.Collection(?:<[^>]*>)?\(\s*['\"]([^'\"]+)")
STRING_LIT = re.compile(r"""["'`]([^"'`\n]{3,120})["'`]""")
TEST_FILE = re.compile(r"\.(spec|test)\.|/tests?(/|\.rs$)")


def actix(path, text):
    if not path.endswith(".rs"):
        return []
    scope = RS_SCOPE.search(text)
    prefix = scope.group(1).rstrip("/") if scope else ""
    return [("http", f"{m.group(1).upper()} {prefix}{m.group(2)}") for m in RS_ROUTE.finditer(text)]


def express(path, text):
    if not path.endswith((".js", ".mjs", ".ts")):
        return []
    return [("http", f"{m.group(1).upper()} {m.group(2)}") for m in EXPRESS_ROUTE.finditer(text)]


def meteor(path, text):
    if not path.endswith((".js", ".mjs", ".ts")):
        return []
    out = [("method", n) for n in meteor_method_names(text)]
    return out + [("publication", m.group(1)) for m in METEOR_PUBLISH.finditer(text)]


def mongo(path, text):
    rx = MONGO_COLL_RS if path.endswith(".rs") else MONGO_COLL_TS
    return [("collection", m.group(1)) for m in rx.finditer(text)]


EXTRACTORS = {"actix": actix, "express": express, "meteor": meteor, "mongo": mongo}


def meteor_method_names(text):
    names = []
    for m in METEOR_METHODS.finditer(text):
        depth, i, start = 1, m.end(), m.end()
        while i < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        d = 0
        for line in text[start : i - 1].splitlines():
            if d == 0:
                km = re.match(r"\s*(?:async\s+)?['\"]?([A-Za-z_][\w.\-]*)['\"]?\s*(?::|\(|,)", line)
                if km:
                    names.append(km.group(1))
            d += line.count("{") - line.count("}")
    return names


def extract(files, names):
    """[(kind, name, path)] for the enabled extractors."""
    fns = [EXTRACTORS[n] for n in names]
    return [(kind, name, path) for path, text in files.items() for fn in fns for kind, name in fn(path, text)]


def route_key(route):
    seg = [s for s in route.strip("/").split("/") if s and not s.startswith(("{", ":"))]
    return seg[-1] if seg else route


def cross_system_deps(files, contract_list, system_of):
    """Consumer file -> provider file, matching string literals and Meteor calls
    against contracts that another system defines."""
    providers = defaultdict(set)
    for kind, name, path in contract_list:
        if kind == "http":
            key = route_key(name.split(" ", 1)[1])
            if len(key) >= 4:
                providers[("http", key)].add(path)
        elif kind in ("method", "publication"):
            providers[("rpc", name)].add(path)
    edges = set()
    for path, text in files.items():
        # Tests mention endpoint names as mock data; that is not a call.
        if TEST_FILE.search(path):
            continue
        system = system_of(path)
        hits = set()
        for m in METEOR_CALL.finditer(text):
            hits |= providers.get(("rpc", m.group(1)), set())
        for m in STRING_LIT.finditer(text):
            lit = m.group(1)
            if re.search(r"\s", lit):
                continue
            for tok in re.split(r"[/?{}]+", lit):
                if not tok or (tok == lit and "_" not in tok and "-" not in tok):
                    continue
                hits |= providers.get(("http", tok), set())
        edges |= {(path, p) for p in hits if system_of(p) != system}
    return edges
