"""TypeScript / JavaScript: imports, with path aliases from tsconfig.json."""

import json
import re
from pathlib import Path

IMPORT = re.compile(r"""(?:import|export)\s[^;]*?from\s+['"]([^'"]+)['"]|import\(\s*['"]([^'"]+)['"]\s*\)""", re.S)


def load_jsonc(text):
    """JSON with comments and trailing commas, as tsconfig allows.
    Comment markers inside strings (`"@both/*"`) are left alone."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        elif text.startswith("//", i):
            i = text.find("\n", i) if "\n" in text[i:] else n
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(c)
            i += 1
    text = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def aliases(files, extra):
    """[(scope dir, alias prefix, target prefix)] from every tsconfig*.json,
    plus repo-wide aliases from the config (scope "")."""
    out = [("", a, t) for a, t in extra.items()]
    for path, text in files.items():
        if not re.search(r"(^|/)tsconfig[^/]*\.json$", path):
            continue
        opts = load_jsonc(text).get("compilerOptions", {})
        scope = str(Path(path).parent)
        scope = "" if scope == "." else scope
        base = normpath(f"{scope}/{opts.get('baseUrl', '.')}")
        for alias, targets in (opts.get("paths") or {}).items():
            if targets:
                a = alias.rstrip("*")
                t = normpath(f"{base}/{targets[0].rstrip('*')}") + ("/" if targets[0].endswith("/*") else "")
                out.append((scope, a, t))
    # Longest alias first so `@/x` does not shadow `@both/x`.
    return sorted(out, key=lambda x: (-len(x[0]), -len(x[1])))


def normpath(p):
    out = []
    for seg in p.split("/"):
        if seg == "..":
            if out:
                out.pop()
        elif seg not in (".", ""):
            out.append(seg)
    return "/".join(out)


def resolve(src, spec, files, alias_list):
    if spec.startswith("."):
        base = str(Path(src).parent / spec)
    else:
        base = next((t + spec[len(a) :] for scope, a, t in alias_list if (not scope or src.startswith(scope + "/")) and spec.startswith(a)), None)
        if base is None:
            return None
    base = normpath(base)
    for cand in (base, base + ".ts", base + ".tsx", base + ".js", base + "/index.ts", base + "/index.js"):
        if cand in files:
            return cand
    return None


def deps(path, text, files, alias_list):
    out = set()
    for m in IMPORT.finditer(text):
        d = resolve(path, m.group(1) or m.group(2), files, alias_list)
        if d and d != path:
            out.add(d)
    return out
