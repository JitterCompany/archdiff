"""RTIC 2 apps: `#[task]`, `#[init]` and `#[idle]` functions, the `Local` and
`Shared` resources that carry channel ends from init to tasks, and `x::spawn(...)`
calls that hand ends to spawned tasks.

Channels are recognised by their constructor. Projects that generate
constructors with macros describe them in `[[system_view.channel_macro]]`:
pattern (regex with `type` and optional `cap` groups) and `ctor`, a format
string such as `create_{type_lower}_channel`, plus optional `tx` / `rx`, the
names of the end types it generates (`{type}Tx`).

Ends are also recognised by their declared type: `Local` / `Shared` fields and
task parameters, through `type` aliases. `[[system_view.channel_end]]` adds
patterns (regex with a `type` group, and `role` tx or rx) for wrapper types.

An interrupt handler is drawn as waking the tasks it shares a peripheral with:
its `local` resources come from the same init binding as a task's resources,
or it calls `Type::isr()` on a type that a task's resources hold.
"""

import re

from .common import balanced, split_top, strip_comments

ITEM = re.compile(r"#\[(task|init|idle)\b")
GENERIC_CTOR = re.compile(r"\b(create_\w+|make_channel!)\s*\(")
TUPLE_LET = re.compile(r"let\s*\(\s*(?:mut\s+)?(\w+)\s*,\s*(?:mut\s+)?(\w+)\s*\)\s*=\s*([\w:!]+)\s*\(")
BLOCK_LET = re.compile(r"let\s*\(\s*(?:mut\s+)?(\w+)\s*,\s*(?:mut\s+)?(\w+)\s*\)\s*=\s*\{")
LET = re.compile(r"let\s+(?:mut\s+)?(\w+)\s*(?::[^=;]+)?=\s*([^;]+);")
SPAWN = re.compile(r"\b(\w+)::spawn\s*\(")
STATIC = re.compile(r"\bstatic\s+([A-Z][A-Z0-9_]*)\s*:\s*([^=]+)=")
SENDER_CALL = re.compile(r"\b(\w+)\.(\w+?)_(?:sender|tx)\s*\(\s*\)")
FIELD_REF = re.compile(r"\bcx\.(?:local|shared)\.(\w+)")
STRUCT = re.compile(r"\bstruct\s+(\w+)\s*(?:<[^{;]*?>)?\s*(?:where[^{;]*)?([{(])")
ALIAS = re.compile(r"\btype\s+(\w+)\s*(?:<[^=;]*>)?\s*=\s*([^;]+);")
TUPLE_ANY = re.compile(r"let\s*\(([^()=;]*)\)\s*=\s*([^;]+);")
FIELD_PATH = re.compile(r"\b([a-z_]\w*)\.([a-z_]\w*)\b(?!\s*[(!:])")
STATIC_CALL = re.compile(r"\b((?:\w+::)*?[A-Z]\w*)(?:::<[^>]*>)?::\w+\s*\(")
BUILTIN_ENDS = [
    {"pattern": r"\bSender<\s*'\w+\s*,\s*(?P<type>\w+)", "role": "tx"},
    {"pattern": r"\bReceiver<\s*'\w+\s*,\s*(?P<type>\w+)", "role": "rx"},
]


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
                for role in ("tx", "rx"):
                    if mac.get(role):
                        out[ctor][role] = mac[role].format(type=ty, type_lower=ty.lower())
    return out


def type_homes(files):
    """type name -> files that define it, following `use x::{A as B}` renames."""
    homes, renames = {}, {}
    for path, text in files.items():
        if not path.endswith(".rs"):
            continue
        text = strip_comments(text)
        for m in re.finditer(r"\b(?:struct|enum|type|trait)\s+([A-Z]\w*)", text):
            homes.setdefault(m.group(1), set()).add(path)
        for use in re.finditer(r"\buse\s+[^;]*;", text):
            for a, b in re.findall(r"\b(\w+)\s+as\s+(\w+)\b", use.group()):
                renames.setdefault(b, set()).add(a)
    for b, srcs in renames.items():
        for a in srcs:
            homes.setdefault(b, set()).update(homes.get(a, set()))
    return homes


def type_aliases(files, crate_prefix):
    """name -> what it is made of: `type` alias bodies and struct field types.
    The app's own crate wins over the rest of the workspace."""
    out = {}
    for path in sorted(files, key=lambda p: not p.startswith(crate_prefix + "/")):
        if not path.endswith(".rs"):
            continue
        text = strip_comments(files[path])
        for m in ALIAS.finditer(text):
            out.setdefault(m.group(1), " ".join(m.group(2).split()))
        for m in STRUCT.finditer(text):
            inner, _ = balanced(text, m.end() - 1, "{", "}") if m.group(2) == "{" else balanced(text, m.end() - 1)
            fields = re.sub(r"#\[[^\]]*\]|\bpub(?:\([^)]*\))?\s", " ", inner)
            out.setdefault(m.group(1), " ".join(re.sub(r"\b\w+\s*:(?!:)", " ", fields).split()))
    return out


def expand_type(ty, aliases, depth=4):
    """The type text plus the bodies of the aliases it reaches."""
    out, todo, seen = [ty], [ty], set()
    for _ in range(depth):
        nxt = []
        for t in todo:
            for w in set(re.findall(r"\b[A-Z]\w*", t)) - seen:
                seen.add(w)
                if w in aliases:
                    nxt.append(aliases[w])
        out += nxt
        todo = nxt
    return " ".join(out)


def struct_fields(text, name):
    """field -> declared type of `struct <name> { ... }`."""
    m = re.search(rf"\bstruct\s+{name}\s*\{{", text)
    if not m:
        return {}
    inner, _ = balanced(text, m.end() - 1, "{", "}")
    inner = re.sub(r"#\[[^\]]*\]", "", inner)
    out = {}
    for field in split_top(inner):
        if fm := re.match(r"(?:pub\s+)?(\w+)\s*:\s*(.+)$", field, re.S):
            out[fm.group(1)] = " ".join(fm.group(2).split())
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


def atoms(expr, origins):
    """The init bindings an expression is built from: `hw.uart` field paths and
    the atoms of the variables it names."""
    out = {f"{a}.{b}" for a, b in FIELD_PATH.findall(expr) if a in origins}
    for ident in re.findall(r"\b[a-z_]\w*\b", FIELD_PATH.sub(" ", expr)):
        out |= origins.get(ident, set())
    return out


def init_origins(body):
    """var -> atoms, in statement order. Bare variables are their own atom only
    through their fields, so one board struct does not link everything."""
    origins = {}
    binds = sorted([(m.start(), [v.strip().removeprefix("mut ").strip() for v in m.group(1).split(",")], m.group(2)) for m in TUPLE_ANY.finditer(body)]
                   + [(m.start(), [m.group(1)], m.group(2)) for m in LET.finditer(body)], key=lambda b: b[0])
    for _, names, expr in binds:
        for n in names:
            if re.fullmatch(r"\w+", n):
                origins.setdefault(n, set())
        got = atoms(expr, origins)
        for n in names:
            if re.fullmatch(r"\w+", n):
                origins[n] = origins[n] | got
    return origins


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
        events = sorted([(m.start(), "pair", m) for m in TUPLE_LET.finditer(body)] + [(m.start(), "let", m) for m in LET.finditer(body)] + [(m.start(), "block", m) for m in BLOCK_LET.finditer(body)], key=lambda e: e[0])
        skip_to = 0
        for start, kind, m in events:
            if start < skip_to:
                continue
            if kind == "block":
                # `let (s, r) = { let (tx, rx) = create_x(); wrap(tx, rx) };`
                inner, skip_to = balanced(body, m.end() - 1, "{", "}")
                inner_scope = scan_body(inner, dict(scope), created)
                carried = ends_of(inner.rsplit(";", 1)[-1], inner_scope)
                tx, rx = m.group(1), m.group(2)
                if carried:
                    scope[tx] = {e for e in carried if e[0] == "tx"}
                    scope[rx] = {e for e in carried if e[0] == "rx"}
            elif kind == "pair":
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

    aliases = type_aliases(files, crate_prefix)
    homes = type_homes(files)
    end_types = {}
    for ctor, info in ctors.items():
        for role in ("tx", "rx"):
            if info.get(role):
                end_types[info[role]] = (role, ctor)
    end_patterns = [(re.compile(e["pattern"]), e["role"]) for e in BUILTIN_ENDS + cfg.get("channel_end", [])]

    def queue_named(ty):
        for ctor, info in ctors.items():
            if info["type"] == ty:
                return new_queue(ctor, "")
        return next((q for q, info in queues.items() if ty in (q, info["type"])), None)

    def typed_ends(ty):
        """Queue ends that a declared type carries."""
        text = expand_type(ty, aliases)
        out = set()
        for w in set(re.findall(r"\b\w+\b", text)):
            if w in end_types:
                role, ctor = end_types[w]
                out.add((role, new_queue(ctor, "")))
        for rx, role in end_patterns:
            for m in rx.finditer(text):
                if q := queue_named(m.group("type")):
                    out.add((role, q))
        return out

    # init first: it creates channels and parks the ends in Local / Shared fields.
    scopes, created_in, origins, field_origin = {}, {}, {}, {}
    for it in items:
        if it["kind"] == "init":
            scopes[it["name"]] = scope = scan_body(it["body"], {}, [])
            origins = init_origins(it["body"])
            for lit in re.finditer(r"\b(Local|Shared)\s*\{", it["body"]):
                inner, _ = balanced(it["body"], lit.end() - 1, "{", "}")
                for field in split_top(inner):
                    fm = re.match(r"(\w+)\s*(?::\s*(.*))?$", field, re.S)
                    if fm:
                        field_ends[fm.group(1)] = ends_of(fm.group(2) or fm.group(1), scope)
                        field_origin[fm.group(1)] = atoms(fm.group(2) or fm.group(1), origins)
    field_types = {**struct_fields(text, "Shared"), **struct_fields(text, "Local")}
    for field, ty in field_types.items():
        field_ends.setdefault(field, set()).update(typed_ends(ty))

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
        for param in split_top(it["params"])[1:]:
            if pm := re.match(r"(?:mut\s+)?\w+\s*:\s*(.+)$", param, re.S):
                for role, q in typed_ends(pm.group(1)):
                    edges.add((name, "q:" + q, "send") if role == "tx" else ("q:" + q, name, "recv"))
        if it["kind"] != "init":
            created_in[name] = []
            scopes[name] = scan_body(it["body"], {}, created_in[name])

    # Interrupt handlers wake the tasks that hold the same peripheral.
    holders = {}
    for it in items:
        if it["kind"] == "task" and "binds" not in tasks[it["name"]]["attrs"]:
            fields = attr_list(it["attr"], "local") + attr_list(it["attr"], "shared")
            holders[it["name"]] = (
                set().union(*(field_origin.get(f, set()) for f in fields)),
                " ".join(expand_type(field_types.get(f, ""), aliases) for f in fields),
            )
    for it in items:
        if "binds" not in tasks[it["name"]]["attrs"]:
            continue
        own = set().union(*(field_origin.get(f, set()) for f in attr_list(it["attr"], "local")))
        types = {m.group(1).split("::")[-1] for m in STATIC_CALL.finditer(it["body"])}
        # A driver's handle often is another type from the same module (`Exti` / `Trigger`).
        isr_homes = set().union(*(homes.get(t, set()) for t in types))
        for task, (origin, held) in holders.items():
            held_homes = set().union(*(homes.get(w, set()) for w in set(re.findall(r"\b[A-Z]\w*", held))))
            by_type = isr_homes & held_homes or any(a.split(".")[-1] == t.lower() for t in types for a in origin)
            if own & origin or by_type:
                edges.add((it["name"], task, "irq"))

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
