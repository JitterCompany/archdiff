"""Tokio-style wiring: an entry file creates `mpsc::channel(N)` pairs and starts
tasks with `module::start_task(...)` / `start_*(...)`, passing queue ends."""

import re

from .common import balanced, split_top, strip_comments

CHANNEL = re.compile(r"let\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)\s*=\s*(?:tokio::sync::)?mpsc::(channel|unbounded_channel)\s*\(\s*(\d*)\s*\)")
START = re.compile(r"(?:let\s+(?:\(([^)]*)\)|(\w+))\s*=\s*)?((?:[a-z_]\w*::)*)(start(?:_\w+)?)\s*(?:::<[^>]*>)?\s*\(")
LET_ALIAS = re.compile(r"let\s+(?:mut\s+)?(\w+)\s*=\s*([^;]+);")


def task_name(module, fn):
    mod = module.rstrip(":").split("::")[-1] if module else ""
    mod = re.sub(r"_task_handler$|_handler$", "", mod)
    rest = re.sub(r"^start_?|_?task$", "", fn)
    if not rest:
        return mod or fn
    if not mod or mod in rest:
        return rest
    return f"{mod}_{rest}"


def find_fn(files, fn, module, prefer_prefix):
    pat = re.compile(rf"\bfn\s+{re.escape(fn)}\b")
    mod = module.rstrip(":").split("::")[-1] if module else None
    cands = [p for p, t in files.items() if p.endswith(".rs") and pat.search(t)]
    if mod:
        cands = [p for p in cands if re.search(rf"/{re.escape(mod)}(\.rs|/mod\.rs)$", p)] or cands
    cands.sort(key=lambda p: (not p.startswith(prefer_prefix), len(p)))
    return cands[0] if cands else None


def fn_params(text, fn):
    m = re.search(rf"\bfn\s+{re.escape(fn)}\b", text)
    if not m:
        return []
    i = m.end()
    while i < len(text) and text[i].isspace():
        i += 1
    if i < len(text) and text[i] == "<":
        _, i = balanced(text, i, "<", ">")
    inner, _ = balanced(text, text.index("(", i))
    params = []
    for p in split_top(strip_comments(inner)):
        if ":" in p:
            name, ty = p.split(":", 1)
            params.append((name.strip().removeprefix("mut "), " ".join(ty.split())))
    return params


def queue_payload(ty):
    """`mpsc::Sender<Foo<Bar>>`, `Arc<Mutex<mpsc::Receiver<Foo>>>` -> `Foo<Bar>`."""
    m = re.search(r"\b(?:Sender|Receiver|UnboundedSender|UnboundedReceiver)\s*<", ty)
    if not m:
        return None
    inner, _ = balanced(ty, m.end() - 1, "<", ">")
    return inner.strip()


def snapshot(files, entry, cfg):
    text = strip_comments(files[entry])
    crate_prefix = entry.rsplit("/src/", 1)[0]
    resource_hints = cfg.get("resources", {})
    io_hints = [(re.compile(p), "ext:" + n) for n, p in cfg.get("io", {}).items()]
    var, queues, tasks, edges = {}, {}, {}, set()

    for m in CHANNEL.finditer(text):
        tx, rx, _, cap = m.groups()
        name = re.sub(r"(^tx_|_tx$|^tx$)", "", tx) or tx
        queues[name] = {"cap": cap or "∞", "type": None}
        var[tx], var[rx] = ("tx", name), ("rx", name)

    def resource_for(expr):
        return next(("ext:" + node for hint, node in resource_hints.items() if hint in expr), None)

    events = sorted([(m.start(), "start", m) for m in START.finditer(text)] + [(m.start(), "alias", m) for m in LET_ALIAS.finditer(text)], key=lambda e: e[0])
    for _, kind, m in events:
        if kind == "alias":
            name, expr = m.groups()
            known = [v for v in re.findall(r"\b\w+\b", expr) if v in var]
            if len(set(known)) == 1 and name not in var:
                var[name] = var[known[0]]
            elif name not in var and (node := resource_for(re.sub(r"\(.*", "", expr))):
                var[name] = ("res", node)
            continue
        tuple_lhs, _, module, fn = m.groups()
        args, _ = balanced(text, m.end() - 1)
        tname = task_name(module, fn)
        file = find_fn(files, fn, module, crate_prefix)
        params = fn_params(files[file], fn) if file else []
        tasks[tname] = {"file": file, "fn": (module or "") + fn, "attrs": {}}
        if tuple_lhs and (node := resource_for((module or "") + fn)):
            var[tuple_lhs.split(",")[0].strip()] = ("res", node)
            edges.add((tname, node, "io"))
        for i, arg in enumerate(split_top(args)):
            panic = "panic" in arg
            for ident in dict.fromkeys(re.findall(r"\b[a-z_]\w*\b", arg)):
                if ident not in var:
                    continue
                role, target = var[ident]
                if role == "tx":
                    edges.add((tname, "q:" + target, "panic" if panic else "send"))
                elif role == "rx":
                    edges.add(("q:" + target, tname, "recv"))
                else:
                    edges.add((tname, target, "io"))
                if role in ("tx", "rx") and not panic and i < len(params):
                    payload = queue_payload(params[i][1])
                    if payload and (role == "rx" or not queues[target]["type"]):
                        queues[target].update(type=payload, ctx=file)
        if file:
            for rx, node in io_hints:
                if rx.search(files[file]):
                    edges.add((tname, node, "io"))

    return {
        "name": crate_prefix.split("/")[-1],
        "kind": "tokio",
        "wiring": entry,
        "tasks": tasks,
        "queues": queues,
        "resources": {},
        "edges": [{"from": a, "to": b, "kind": k} for a, b, k in sorted(edges)],
    }


def score(text):
    return len(CHANNEL.findall(text)) + text.count("start_task(")
