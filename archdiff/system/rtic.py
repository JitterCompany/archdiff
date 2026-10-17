"""RTIC 2 apps: `#[task]`, `#[init]` and `#[idle]` functions, the `Local` and
`Shared` resources that carry channel ends from init to tasks, and `x::spawn(...)`
calls that hand ends to spawned tasks.

Channels are recognised by their constructor. Projects that generate
constructors with macros describe them in `[[system_view.channel_macro]]`:
pattern (regex with `type` and optional `cap` groups) and `ctor`, a format
string such as `create_{type_lower}_channel`.
"""

import re

from .common import balanced, split_top, strip_comments

ITEM = re.compile(r"#\[(task|init|idle)\b")
GENERIC_CTOR = re.compile(r"\b(create_\w+|make_channel!)\s*\(")
TUPLE_LET = re.compile(r"let\s*\(\s*(?:mut\s+)?(\w+)\s*,\s*(?:mut\s+)?(\w+)\s*\)\s*=\s*([\w:!]+)\s*\(")
LET = re.compile(r"let\s+(?:mut\s+)?(\w+)\s*(?::[^=;]+)?=\s*([^;]+);")
SPAWN = re.compile(r"\b(\w+)::spawn\s*\(")
STATIC = re.compile(r"\bstatic\s+([A-Z][A-Z0-9_]*)\s*:\s*([^=]+)=")
SENDER_CALL = re.compile(r"\b(\w+)\.(\w+?)_(?:sender|tx)\s*\(\s*\)")
FIELD_REF = re.compile(r"\bcx\.(?:local|shared)\.(\w+)")


def channel_macros(files, crate_prefix, entry, macros):
    """ctor name -> {type, cap}; the entry file wins, then the app's own crate."""
    out = {}
    def precedence(p):
        return (p.startswith(crate_prefix + "/"), p == entry)

    for path in sorted(files, key=precedence):
        if not path.endswith(".rs"):
            continue
        text = strip_comments(files[path])
        for mac in macros:
            for m in re.finditer(mac["pattern"], text):
                ty = m.group("type")
                cap = m.groupdict().get("cap") or mac.get("cap", "?")
                ctor = mac["ctor"].format(type=ty, type_lower=ty.lower())
                out[ctor] = {"type": ty, "cap": " ".join(str(cap).split()).strip("{} ")}
    return out


def parse_items(text):
    """[{kind, name, attrs, params, body}] for RTIC task-like functions."""
    items = []
    for m in ITEM.finditer(text):
        kind = m.group(1)
        i = m.end()
        attr = ""
        if text[i] == "(":
            attr, i = balanced(text, i)
        fm = re.compile(r"\bfn\s+(\w+)\s*").search(text, i)
        if not fm:
            continue
        params, j = balanced(text, text.index("(", fm.end()))
        nxt = re.compile(r"[{;]").search(text, j)
        body = ""
        if nxt and nxt.group() == "{":
            body, _ = balanced(text, nxt.start(), "{", "}")
        items.append({"kind": kind, "name": fm.group(1), "attr": attr, "params": params, "body": body})
    return items


def attr_list(attr, key):
    m = re.search(rf"\b{key}\s*=\s*\[", attr)
    if not m:
        return []
    inner, _ = balanced(attr, m.end() - 1, "[", "]")
    return [re.match(r"&?\s*(\w+)", x).group(1) for x in split_top(inner) if re.match(r"&?\s*\w+", x)]


def snapshot(files, entry, cfg):
    text = strip_comments(files[entry])
    crate_prefix = entry.rsplit("/src/", 1)[0]
    ctors = channel_macros(files, crate_prefix, entry, cfg.get("channel_macro", []))
    statics = {m.group(1): m.group(2) for m in STATIC.finditer(text)}
    items = parse_items(text)
    queues, tasks, resources, edges = {}, {}, {}, set()
    field_ends = {}

    def new_queue(ctor, args):
        if ctor in ctors:
            name, info = ctors[ctor]["type"], ctors[ctor]
            queues.setdefault(name, {"cap": info["cap"], "type": info["type"], "ctx": entry})
            return name
        static = re.search(r"&\s*([A-Z][A-Z0-9_]*)", args)
        if static and static.group(1) in statics:
            name = static.group(1).removesuffix("_CHANNEL").removesuffix("_SIGNAL").lower()
            ty = re.findall(r"<[^<>]*?,\s*([\w:]+)\s*>", statics[static.group(1)])
            queues.setdefault(name, {"cap": "latest", "type": ty[-1] if ty else None, "ctx": entry})
            return name
        name = re.sub(r"^create_|_(channel|signal)$|!$", "", ctor)
        queues.setdefault(name, {"cap": "?", "type": None, "ctx": entry})
        return name

    def ends_of(expr, scope):
        """Queue ends that an expression carries in this scope."""
        out = set()
        for m in SENDER_CALL.finditer(expr):
            carried = {e for e in scope.get(m.group(1), set()) if e[0] == "tx"}
            stem = m.group(2).replace("_", "").lower()
            out |= {e for e in carried if stem in e[1].replace("_", "").lower()} or carried
        rest = SENDER_CALL.sub(" ", expr)
        for f in FIELD_REF.findall(rest):
            out |= field_ends.get(f, set())
        for ident in re.findall(r"\b[a-z_]\w*\b", FIELD_REF.sub(" ", rest)):
            out |= scope.get(ident, set())
        return out

    def scan_body(body, scope, created):
        """Bindings in statement order: channel pairs and aliases."""
        events = sorted([(m.start(), "pair", m) for m in TUPLE_LET.finditer(body)] + [(m.start(), "let", m) for m in LET.finditer(body)], key=lambda e: e[0])
        for _, kind, m in events:
            if kind == "pair":
                tx, rx, ctor = m.groups()
                if ctor in ctors or GENERIC_CTOR.match(ctor + "("):
                    args, _ = balanced(body, m.end() - 1)
                    q = new_queue(ctor, args)
                    scope[tx], scope[rx] = {("tx", q)}, {("rx", q)}
                    created.extend([(tx, "tx", q), (rx, "rx", q)])
            else:
                name, expr = m.groups()
                carried = ends_of(expr, scope)
                if carried:
                    scope[name] = carried
        return scope

    # init first: it creates channels and parks the ends in Local / Shared fields.
    scopes, created_in = {}, {}
    for it in items:
        if it["kind"] == "init":
            scopes[it["name"]] = scope = scan_body(it["body"], {}, [])
            for lit in re.finditer(r"\b(Local|Shared)\s*\{", it["body"]):
                inner, _ = balanced(it["body"], lit.end() - 1, "{", "}")
                for field in split_top(inner):
                    fm = re.match(r"(\w+)\s*(?::\s*(.*))?$", field, re.S)
                    if fm:
                        field_ends[fm.group(1)] = ends_of(fm.group(2) or fm.group(1), scope)

    for it in items:
        name = it["name"]
        attrs = {}
        if p := re.search(r"\bpriority\s*=\s*(\d+)", it["attr"]):
            attrs["priority"] = p.group(1)
        if b := re.search(r"\bbinds\s*=\s*(\w+)", it["attr"]):
            attrs["binds"] = b.group(1)
        local, shared = attr_list(it["attr"], "local"), attr_list(it["attr"], "shared")
        if shared:
            attrs["shared"] = ", ".join(sorted(shared))
        tasks[name] = {"file": entry, "fn": name, "kind": it["kind"], "attrs": attrs, "body": it["body"]}
        for field in local + shared:
            for role, q in field_ends.get(field, set()):
                edges.add((name, "q:" + q, "send") if role == "tx" else ("q:" + q, name, "recv"))
        for r in shared:
            resources[r] = {}
            edges.add((name, "r:" + r, "shared"))
        if it["kind"] != "init":
            created_in[name] = []
            scopes[name] = scan_body(it["body"], {}, created_in[name])

    for it in items:
        scope = scopes.get(it["name"], {})
        handed = set()
        for m in SPAWN.finditer(it["body"]):
            target = m.group(1)
            if target not in tasks:
                continue
            edges.add((it["name"], target, "spawn"))
            args, _ = balanced(it["body"], m.end() - 1)
            handed |= set(re.findall(r"\b\w+\b", args))
            for role, q in ends_of(args, scope):
                edges.add((target, "q:" + q, "send") if role == "tx" else ("q:" + q, target, "recv"))
        # A task that creates a channel keeps the ends it does not hand to a spawn.
        for var, role, q in created_in.get(it["name"], []):
            if var not in handed:
                edges.add((it["name"], "q:" + q, "send") if role == "tx" else ("q:" + q, it["name"], "recv"))

    stem = entry.rsplit("/src/", 1)
    return {
        "name": stem[0].split("/")[-1],
        "kind": "rtic",
        "wiring": entry,
        "tasks": tasks,
        "queues": queues,
        "resources": resources,
        "edges": [{"from": a, "to": b, "kind": k} for a, b, k in sorted(edges)],
    }


def score(text):
    return 10 if re.search(r"#\[(?:rtic::)?app\s*\(", text) else 0
