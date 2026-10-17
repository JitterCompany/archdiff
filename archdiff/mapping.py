"""Map repository paths to logical paths. The first segment of a logical path
is the L0 system, deeper segments are containers, modules and units."""

import re


def glob_re(glob):
    out, i = "", 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif glob.startswith("**", i):
            out += ".*"
            i += 2
        elif glob[i] == "*":
            out += "[^/]*"
            i += 1
        else:
            out += re.escape(glob[i])
            i += 1
    return re.compile("^" + out + "$")


def rule_re(pattern):
    """`a/{name}/src/{*rest}`: `{x}` matches one segment, `{*x}` the remainder."""
    parts = re.split(r"(\{\*?\w+\})", pattern)
    out = ""
    for p in parts:
        m = re.fullmatch(r"\{(\*?)(\w+)\}", p)
        if m:
            out += f"(?P<{m.group(2)}>.*)" if m.group(1) else f"(?P<{m.group(2)}>[^/]+)"
        else:
            out += re.escape(p)
    return re.compile("^" + out + "$")


class Mapper:
    def __init__(self, cfg):
        self.rules = [(rule_re(r["from"]), r["to"]) for r in cfg["map"]]
        self.ignore = [glob_re(g) for g in cfg["ignore"]]
        self.default_map = cfg["default_map"]
        self.source = tuple(cfg["source"])
        self._cache = {}

    def __call__(self, path):
        if path not in self._cache:
            self._cache[path] = self._map(path)
        return self._cache[path]

    def _map(self, path):
        if any(r.match(path) for r in self.ignore):
            return None
        for rx, to in self.rules:
            m = rx.match(path)
            if m:
                return collapse(to.format(**m.groupdict()))
        if self.default_map and path.endswith(self.source):
            return collapse("/".join(s for s in path.split("/") if s != "src"))
        return None


def collapse(lp):
    """Group files that form one unit: `foo.rs` + `foo/`, `mod.rs`/`lib.rs`/`main.rs`
    as their directory, and `foo.component.{ts,html,scss,spec.ts}` as `foo.component`."""
    parts = lp.split("/")
    name = parts[-1]
    if name in ("mod.rs", "lib.rs", "main.rs") and len(parts) > 1:
        return "/".join(parts[:-1])
    if name.endswith(".rs"):
        parts[-1] = name[:-3]
        return "/".join(parts)
    m = re.match(r"^(.*?)(\.spec|\.test)?\.(ts|tsx|js|jsx|mjs|html|scss|css)$", name)
    if m:
        parts[-1] = m.group(1)
    return "/".join(parts)
