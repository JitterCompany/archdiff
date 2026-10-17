"""Shared parts of the runtime view: parsing helpers, message-type resolution
and the diff between two snapshots.

A snapshot is one app: {name, wiring, tasks{name: {...}}, queues{name: {...}},
resources{name: {...}}, edges[{from, to, kind}]}. Queue nodes are `q:<name>`,
shared resources `r:<name>`, infrastructure `ext:<name>`.
"""

import difflib
import re

TYPE_DEF = re.compile(r"\b(enum|struct)\s+([A-Z]\w*)\s*(?:<[^{;]*?>)?\s*(?:where[^{;]*)?\{")
TYPE_ALIAS = re.compile(r"\btype\s+([A-Z]\w*)\s*(?:<[^=;]*>)?\s*=\s*([^;]+);")


def balanced(text, start, open_ch="(", close_ch=")"):
    """(inner text, end index) of the bracket group that opens at text[start]."""
    depth, i = 0, start
    while i < len(text):
        if text[i] == open_ch:
            depth += 1
        elif text[i] == close_ch:
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
        i += 1
    return text[start + 1 :], len(text)


def split_top(text, sep=","):
    out, depth, cur = [], 0, ""
    for ch in text:
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
        if ch == sep and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return [c.strip() for c in out]


def strip_comments(text):
    return re.sub(r"//[^\n]*", "", text)


def crate_of(path):
    m = re.match(r"(?:[^:]+:)?(.*?)/src/", path)
    return m.group(1) if m else path


def type_index(files):
    """name -> [{kind, file, members{name: text} | target}]."""
    idx = {}
    for path, text in files.items():
        if not path.endswith(".rs") or "/tests" in path:
            continue
        clean = strip_comments(text)
        for m in TYPE_DEF.finditer(clean):
            kind, name = m.groups()
            body, _ = balanced(clean, m.end() - 1, "{", "}")
            members = {}
            for item in split_top(body):
                item = re.sub(r"#\[[^\]]*\]", "", item).strip()
                mm = re.match(r"(?:pub(?:\([^)]*\))?\s+)?([A-Za-z_]\w*)", item)
                if mm:
                    members[mm.group(1)] = " ".join(item.split())
            idx.setdefault(name, []).append({"kind": kind, "file": path, "members": members})
        for m in TYPE_ALIAS.finditer(clean):
            idx.setdefault(m.group(1), []).append({"kind": "type", "file": path, "target": " ".join(m.group(2).split())})
    return idx


def pick(defs, context, exclude):
    """Among same-named definitions prefer the context crate, and never the
    alias being expanded (an alias often shadows its generic target)."""
    cands = [d for d in defs if d["file"] != exclude] or defs
    cands.sort(key=lambda d: (crate_of(d["file"]) != context, d["kind"] == "type"))
    return cands[0]


def resolve_types(payload, idx, context, depth=2):
    """Definitions reachable from a message type, following aliases.
    Keyed `Name@crate`, so a type that moves between crates diffs as a move."""
    out, todo = {}, [(payload, 0, context, None)]
    while todo:
        ty, d, ctx, exclude = todo.pop(0)
        for name in dict.fromkeys(re.findall(r"\b([A-Z]\w*)\b", ty or "")):
            # Short all-caps names (D, DT, EV) are generic parameters, not message types.
            if name not in idx or d > depth or re.fullmatch(r"[A-Z]{1,3}", name):
                continue
            defn = pick(idx[name], ctx, exclude)
            key = f"{name}@{crate_of(defn['file']).split('/')[-1]}"
            if key in out:
                continue
            out[key] = defn | {"depth": d}
            if defn["kind"] == "type":
                todo.append((defn["target"], d, crate_of(defn["file"]), defn["file"]))
            else:
                for text in defn["members"].values():
                    todo.append((text, d + 1, crate_of(defn["file"]), None))
    return out


def attach_types(app, files):
    idx = type_index(files)
    for q in app["queues"].values():
        q["defs"] = resolve_types(q["type"], idx, crate_of(q.get("ctx") or app["wiring"])) if q.get("type") else {}


# ---------- diff ----------


def diff_types(base_defs, head_defs):
    def by_name(defs):
        out = {}
        for key, d in defs.items():
            out.setdefault(key.split("@")[0], []).append(key)
        return out

    out = []
    for key in sorted(set(base_defs) | set(head_defs)):
        b, h = base_defs.get(key), head_defs.get(key)
        name, crate = key.split("@")
        other = by_name(head_defs if b else base_defs).get(name, [])
        # Types deep in the closure only matter when they change, not when they come or go.
        if bool(b) != bool(h) and (b or h)["depth"] > 1 and not other:
            continue
        entry = {"key": key, "name": name, "crate": crate, "kind": (h or b)["kind"]}
        if b and h:
            entry["status"] = "same"
        elif b:
            if [k for k in other if k not in base_defs]:
                continue  # reported from the head side as a move
            entry["status"] = "del"
        else:
            src = [k for k in other if k not in head_defs]
            entry["status"] = "move" if src else "add"
            if src:
                entry["from_crate"] = src[0].split("@")[1]
                b = base_defs[src[0]]
        if b and h:
            bm, hm = b.get("members", {}), h.get("members", {})
            entry["members_added"] = sorted(set(hm) - set(bm))
            entry["members_removed"] = sorted(set(bm) - set(hm))
            entry["members_changed"] = sorted(k for k in set(bm) & set(hm) if bm[k] != hm[k])
            if b.get("target") != h.get("target"):
                entry["target"] = [b.get("target"), h.get("target")]
            if b["kind"] != h["kind"]:
                entry["kind_change"] = [b["kind"], h["kind"]]
            touched = entry["members_added"] or entry["members_removed"] or entry["members_changed"] or "target" in entry or "kind_change" in entry
            if touched and entry["status"] == "same":
                entry["status"] = "changed"
        entry["members"] = sorted((h or b).get("members", {}))
        entry["target_now"] = (h or b).get("target")
        out.append(entry)
    return out


def body_churn(b, h):
    """Lines added / removed in a task's own body, for frameworks that keep all
    tasks in one file (where file churn says nothing about a single task)."""
    if not b and not h:
        return None
    if "body" not in (b or h):
        return None
    old = (b or {}).get("body", "").splitlines()
    new = (h or {}).get("body", "").splitlines()
    add = dele = 0
    for line in difflib.unified_diff(old, new, lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            add += 1
        elif line.startswith("-") and not line.startswith("---"):
            dele += 1
    return {"add": add, "del": dele}


EMPTY = {"tasks": {}, "queues": {}, "resources": {}, "edges": [], "wiring": None}


def pair_apps(base_apps, head_apps):
    """Match apps by name; a single leftover on each side is the same app renamed."""
    b, h = {a["name"]: a for a in base_apps}, {a["name"]: a for a in head_apps}
    pairs = [(b[n], h[n]) for n in b if n in h]
    lb, lh = [b[n] for n in b if n not in h], [h[n] for n in h if n not in b]
    if len(lb) == 1 and len(lh) == 1:
        return pairs + [(lb[0], lh[0])]
    return pairs + [(a, None) for a in lb] + [(None, a) for a in lh]


def diff_app(base, head, renames, mapper):
    base, head = base or EMPTY, head or EMPTY

    # A task whose file was renamed keeps its identity; failing that, match
    # unpaired tasks on start function and connections.
    rename = {}
    head_by_key = {(t["file"], t["fn"]): n for n, t in head["tasks"].items()}
    for n, t in base["tasks"].items():
        new = head_by_key.get((renames.get(t["file"], t["file"]), t["fn"]))
        if new and new != n and n not in head["tasks"]:
            rename[n] = new

    def links(view, n):
        return {(e["to"] if e["from"] == n else e["from"]) for e in view["edges"] if n in (e["from"], e["to"]) and not e["to"].startswith("ext:")}

    gone = [n for n in base["tasks"] if n not in head["tasks"] and n not in rename]
    new = [n for n in head["tasks"] if n not in base["tasks"] and n not in rename.values()]
    for g in gone:
        for n in new:
            same_fn = base["tasks"][g]["fn"].split("::")[-1] == head["tasks"][n]["fn"].split("::")[-1]
            lb, lh = links(base, g), links(head, n)
            if same_fn and lb and len(lb & lh) / len(lb | lh) >= 0.5:
                rename[g] = n
                new.remove(n)
                break
    rn = lambda n: rename.get(n, n)

    tasks = []
    for n in sorted(set(map(rn, base["tasks"])) | set(head["tasks"])):
        b = next((t for k, t in base["tasks"].items() if rn(k) == n), None)
        h = head["tasks"].get(n)
        t = h or b
        attrs = {k: [(b or {}).get("attrs", {}).get(k), (h or {}).get("attrs", {}).get(k)] for k in set((b or {}).get("attrs", {})) | set((h or {}).get("attrs", {}))}
        tasks.append({
            "churn": body_churn(b, h),
            "name": n,
            "status": "add" if not b else "del" if not h else "same",
            "renamed_from": next((k for k, v in rename.items() if v == n), None),
            "fn": t["fn"],
            "file": t["file"],
            "unit": mapper(t["file"]) if t["file"] else None,
            "attrs": {k: v for k, v in attrs.items()},
            "attr_changes": sorted(k for k, (x, y) in attrs.items() if b and h and x != y),
        })

    queues = []
    for n in sorted(set(base["queues"]) | set(head["queues"])):
        b, h = base["queues"].get(n), head["queues"].get(n)
        types = diff_types((b or {}).get("defs", {}), (h or {}).get("defs", {}))
        queues.append({
            "name": n,
            "status": "add" if not b else "del" if not h else "same",
            "cap": [(b or {}).get("cap"), (h or {}).get("cap")],
            "type": [(b or {}).get("type"), (h or {}).get("type")],
            "types": types,
        })

    resources = [{"name": n, "status": "add" if n not in base["resources"] else "del" if n not in head["resources"] else "same"}
                 for n in sorted(set(base["resources"]) | set(head["resources"]))]

    key = lambda e: (rn(e["from"]), rn(e["to"]), e["kind"])
    be, he = {key(e) for e in base["edges"]}, {key(e) for e in head["edges"]}
    edges = [{"from": a, "to": b, "kind": k, "s": "same" if (a, b, k) in be and (a, b, k) in he else "add" if (a, b, k) in he else "del"}
             for a, b, k in sorted(be | he)]
    return {
        "name": (head or base).get("name") or (base.get("name")),
        "renamed_from": base.get("name") if base.get("name") and head.get("name") and base["name"] != head["name"] else None,
        "kind": (head or base).get("kind"),
        "wiring": [base["wiring"], head["wiring"]],
        "tasks": tasks,
        "queues": queues,
        "resources": resources,
        "edges": edges,
    }
