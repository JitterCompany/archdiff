"""Markdown summary for a PR comment: the headlines of both views, in a few lines."""

LEVELS = ["system", "container", "module", "unit"]
MAX = 6


def level(depth):
    return f"L{depth} {LEVELS[min(depth, len(LEVELS) - 1)]}"


def capped(lines):
    return lines[:MAX] + ([f"- … and {len(lines) - MAX} more"] if len(lines) > MAX else [])


def short(rev):
    return (rev or "none")[:7]


def structural(edges):
    """Smallest depth at which a dependency between distinct units appeared or vanished."""
    depth = max((e["from"].count("/") for e in edges), default=0) + 1
    for d in range(1, depth + 1):
        cut = lambda p: "/".join(p.split("/")[:d])
        groups = {}
        for e in edges:
            a, b = cut(e["from"]), cut(e["to"])
            if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                continue
            groups.setdefault((a, b), set()).add(e["s"])
        hits = [(a, b, "add" in s) for (a, b), s in groups.items() if "same" not in s]
        if hits:
            return d - 1, sorted(hits)
    return None


def top(paths):
    return [p for p in paths if not any(q != p and p.startswith(q + "/") for q in paths)]


def code_lines(d):
    out = []
    for lib in d["libs"]:
        if lib["base"] and lib["head"] and lib["base"] != lib["head"]:
            defs = sum(t["how"] == "definition" for t in lib["touched"])
            new = f", starts using {len(lib['new_in_bump'])} new item(s)" if lib["new_in_bump"] else ""
            out.append(f"- Bump **{lib['repo']}** `{short(lib['base'])}` → `{short(lib['head'])}`: "
                       f"touches {defs} of {lib['used']} used items{new}")
        elif not lib["base"]:
            out.append(f"- New git dependency **{lib['repo']}** `{short(lib['head'])}`")
    out += capped([f"- Moved `{m['from']}` ⇒ `{m['to']}` ({m['files']} files)" for m in d["moves"]])
    units = [f"- New `{p}`" for p in top(d["added"])] + [f"- Removed `{p}`" for p in top(d["removed"])]
    out += capped(units)
    s = structural(d["edges"])
    if s:
        lvl, hits = s
        out.append(f"- Dependency changes at {level(lvl)}:")
        out += ["  " + l for l in capped([f"- {'+' if add else '−'} `{a}` → `{b}`" for a, b, add in hits])]
    out += capped([f"- Contract {'+' if c['s'] == 'add' else '−'} {c['kind']} `{c['name']}`" for c in d["contracts"]])
    return out


def app_lines(app):
    out = []
    for t in app["tasks"]:
        if t["status"] == "add":
            out.append(f"- + task `{t['name']}`")
        elif t["status"] == "del":
            out.append(f"- − task `{t['name']}`")
        if t["renamed_from"]:
            out.append(f"- task `{t['renamed_from']}` ⇒ `{t['name']}`")
        for k in t["attr_changes"]:
            x, y = t["attrs"][k]
            out.append(f"- task `{t['name']}` {k} {x} → {y}")
    for q in app["queues"]:
        if q["status"] != "same":
            out.append(f"- {'+' if q['status'] == 'add' else '−'} queue `{q['name']}`")
        elif q["cap"][0] != q["cap"][1]:
            out.append(f"- queue `{q['name']}` capacity {q['cap'][0]} → {q['cap'][1]}")
        changed = [t for t in q["types"] if t["status"] not in ("same", "move") or t.get("members_changed") or t.get("members_added") or t.get("members_removed")]
        if changed and q["status"] == "same":
            names = list(dict.fromkeys(t["name"] for t in changed))
            out.append(f"- message types on `{q['name']}` changed: " + ", ".join(f"`{n}`" for n in names[:4]) + (" …" if len(names) > 4 else ""))
    conn = [e for e in app["edges"] if e["s"] != "same"]
    if conn:
        add = sum(e["s"] == "add" for e in conn)
        out.append(f"- connections +{add} −{len(conn) - add}")
    return capped(out)


def markdown(d, link=None):
    lines = ["### Architecture diff", ""]
    if link:
        lines += [f"[Open the interactive diff]({link})", ""]
    code = code_lines(d)
    lines += ["**Code**"] + (code or ["- No structural changes. Internal edits only."]) + [""]
    if d.get("system"):
        changed = [(a, app_lines(a)) for a in d["system"]["apps"]]
        changed = [(a, l) for a, l in changed if l]
        lines.append("**System**")
        if not changed:
            lines.append("- Same tasks, queues and message types.")
        for app, l in changed:
            if len(d["system"]["apps"]) > 1:
                lines.append(f"- app `{app['name']}`")
                lines += ["  " + x for x in l]
            else:
                lines += l
    return "\n".join(lines) + "\n"
