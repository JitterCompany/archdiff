"""archdiff BASE HEAD: architecture-level diff of two revisions as an HTML page.

  python -m archdiff origin/main HEAD --repo . --out diff.html
"""

import argparse
import json
from pathlib import Path

from . import config
from .build import build
from .summary import markdown

VIEWER = Path(__file__).parent / "viewer.html"


def main():
    ap = argparse.ArgumentParser(prog="archdiff", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base")
    ap.add_argument("head")
    ap.add_argument("--repo", default=".", help="repository to analyse (default: current directory)")
    ap.add_argument("--config", help="config file (default: <repo>/archdiff.toml)")
    ap.add_argument("--title", default="")
    ap.add_argument("--title-url", help="link for the title, e.g. the pull request")
    ap.add_argument("--out", help="HTML output file")
    ap.add_argument("--json", help="also write the extracted data as JSON")
    ap.add_argument("--summary", help="also write a markdown summary, e.g. for a PR comment")
    ap.add_argument("--link", help="URL of the HTML page, included in the summary")
    args = ap.parse_args()

    cfg = config.load(args.config or Path(args.repo) / "archdiff.toml")
    data = build(args.repo, args.base, args.head, cfg, args.title)
    data["title_url"] = args.title_url
    if args.json:
        Path(args.json).write_text(json.dumps(data, indent=1))
    if args.out:
        # `<\/` keeps a PR title or path containing `</script>` inside the JSON.
        payload = json.dumps(data).replace("</", "<\\/")
        Path(args.out).write_text(VIEWER.read_text().replace("/*DATA*/null", payload))
    if args.summary:
        Path(args.summary).write_text(markdown(data, args.link))
    n_add = sum(e["s"] == "add" for e in data["edges"])
    n_del = sum(e["s"] == "del" for e in data["edges"])
    apps = len(data["system"]["apps"]) if data["system"] else 0
    print(f"{args.out or args.json}: {len(data['nodes'])} nodes, edges +{n_add} -{n_del}, "
          f"{len(data['contracts'])} contract changes, {apps} system app(s)")


if __name__ == "__main__":
    main()
