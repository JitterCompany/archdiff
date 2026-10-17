import re
import subprocess


class Git:
    def __init__(self, repo, git_dir=None):
        self.repo = str(repo)
        self.git_dir = str(git_dir) if git_dir else None

    def _cmd(self, *args):
        pre = ["--git-dir", self.git_dir] if self.git_dir else ["-C", self.repo]
        return ["git", *pre, *args]

    def run(self, *args, check=True):
        return subprocess.run(self._cmd(*args), capture_output=True, text=True, check=check).stdout

    def ok(self, *args):
        return subprocess.run(self._cmd(*args), capture_output=True).returncode == 0

    def read_tree(self, rev, keep):
        """{path: text} for the files at rev that `keep(path)` accepts."""
        names = [p for p in self.run("ls-tree", "-r", "--name-only", rev).splitlines() if keep(p)]
        if not names:
            return {}
        proc = subprocess.run(
            self._cmd("cat-file", "--batch"),
            input=("\n".join(f"{rev}:{n}" for n in names) + "\n").encode(),
            capture_output=True,
            check=True,
        )
        out, pos, files = proc.stdout, 0, {}
        for name in names:
            nl = out.index(b"\n", pos)
            size = int(out[pos:nl].decode().split()[2])
            files[name] = out[nl + 1 : nl + 1 + size].decode("utf-8", "replace")
            pos = nl + 1 + size + 1
        return files

    def changes(self, base, head, paths=()):
        """Return (status {path: A|M|D|R}, renames {old: new}, numstat [(path, add, del)])."""
        status, renames = {}, {}
        for line in self.run("diff", "--name-status", "-M", base, head, "--", *paths).splitlines():
            parts = line.split("\t")
            status[parts[-1]] = parts[0][0]
            if parts[0].startswith("R"):
                renames[parts[1]] = parts[2]
        numstat = []
        for line in self.run("diff", "--numstat", "-M", base, head, "--", *paths).splitlines():
            add, dele, path = line.split("\t")
            if " => " in path:
                path = re.sub(r"\{([^}]*) => ([^}]*)\}", r"\2", path).replace("//", "/")
                path = path.split(" => ")[-1]
            numstat.append((path, int(add) if add != "-" else 0, int(dele) if dele != "-" else 0))
        return status, renames, numstat
