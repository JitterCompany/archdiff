# archdiff

Review a PR at the level of its architecture instead of its lines. archdiff compares two git revisions and renders one HTML page with two views:

- **Code**: systems, crates and modules, and the dependencies between them. It zooms in automatically where the structure changed. New and removed dependencies, moved units, contract changes (HTTP routes, RPC methods, collections) and git-dependency bumps are highlighted.
- **System**: the running tasks, the queues between them and the message types on each queue. Supports tokio-style servers and RTIC firmware.

Everything project-specific lives in an `archdiff.toml` in the analysed repo. The tool itself has no project knowledge.

## Usage

```sh
python3 -m archdiff BASE HEAD --repo path/to/repo --out diff.html [--json diff.json] [--title "PR #12"]
```

Python 3.11+, standard library only. The page loads Mermaid from a CDN.

Git dependencies (`git = ...` in Cargo.toml) are read from cargo's cache in `~/.cargo/git/db`. Run `cargo fetch` first. A base rev that is missing is fetched into that cache.

## In CI

The repo is also a GitHub Action. It generates the page for a pull request, rsyncs it to your host and posts (or updates) one PR comment with the summary and the link. The tag in `uses:` pins the tool version.

```yaml
on:
  pull_request:
permissions:
  contents: read
  pull-requests: write
concurrency:
  group: archdiff-${{ github.event.pull_request.number }}
  cancel-in-progress: true
jobs:
  archdiff:
    runs-on: ubuntu-latest
    if: github.event.pull_request.head.repo.full_name == github.repository  # forks get no secrets
    steps:
      - uses: actions/checkout@v6
        with:
          fetch-depth: 0
      - uses: JitterCompany/archdiff@v0.1.2
        with:
          site: archdiff.example.com
          name: my-repo
          upload-key: ${{ secrets.ARCHDIFF_UPLOAD_KEY }}
          known-hosts: ${{ secrets.ARCHDIFF_KNOWN_HOSTS }}
```

The host (nginx, GitHub login, upload key restricted with rrsync) is in [`host/`](host/).

- [docs/setup.md](docs/setup.md): the full walk-through, from config to the first PR comment, with troubleshooting.
- [docs/faq.md](docs/faq.md): how it decides what to show, and why the setup looks the way it does.

## Config

`archdiff.toml` in the repo root. All keys are optional.

```toml
# Paths that never count.
ignore = ["**/target/**", "**/node_modules/**"]

# Ordered path rules. {x} matches one segment, {*x} the rest. First match wins.
# The first segment of the result is the top-level system.
[[map]]
from = "server/crates/{crate}/src/{*rest}"
to = "server/{crate}/{rest}"

# Without a rule, source files map to their path with `src/` dropped.
# Set to false to only include mapped paths.
default_map = true

[labels]
server = "Backend"

# TypeScript path aliases are read from tsconfig.json. Extra ones:
[ts.aliases]
"/imports/" = "api/server/imports/"

# Infrastructure, recognised by a regex in the source.
[[infra]]
node = "db"
label = "Database"
pattern = '\bmongodb::'

[contracts]
extractors = ["actix", "express", "meteor", "mongo"]
cross_system = true   # link callers to routes defined in another system
```

### System view

tokio: an entry file creates `mpsc::channel(N)` pairs and starts tasks with `module::start_task(...)` or `start_*(...)`.

```toml
[system_view]
kind = "tokio"
entry = ["server/**/main.rs"]
[system_view.resources]   # handle created by a call whose name contains the key
db = "db"
[system_view.io]          # I/O a task does itself, regex on its module
mqtt = '\brumqttc::'
```

RTIC 2: `#[task]`, `#[init]` and `#[idle]` items, channel ends passed through `Local` / `Shared` fields or `spawn(...)` arguments. An end is recognised by how it is built (`let (tx, rx) = create_x_channel()`) and by its declared type: field types of `Local` / `Shared` and task parameters, through `type` aliases. `rtic_sync` `Sender` / `Receiver` work out of the box. Macros and wrapper types are declared here:

```toml
[system_view]
kind = "rtic"
entry = ["apps/*/src/main.rs"]
[[system_view.channel_macro]]
pattern = 'define_channel!\(\s*(?P<type>\w+)\s*,\s*(?P<cap>.+?)\)\s*;'
ctor = "create_{type_lower}_channel"
tx = "{type}Tx"           # end types the macro generates (optional)
rx = "{type}Rx"
[[system_view.channel_end]] # any other type that carries an end
pattern = "LatestValueSender<\\s*'static\\s*,\\s*(?P<type>\\w+)\\s*>"
role = "tx"
```

Interrupt handlers (`binds = ...`) get a dashed edge to the tasks they wake. A handler wakes a task when:

- its `local` resources come from the same init binding as the task's resources (`let (uart, uart_isr) = hw.uart;`), or
- it calls `Type::isr()` and the task's resources hold that type, or another type from the same module (`Exti::isr()` and an `exti::Trigger`).

This is a heuristic: a shared bus links every task that holds a device on it.

Every entry file is one app. The page has an app picker.

## Limits

- Imports are found with regexes, not a compiler. Macros that generate modules or tasks are invisible unless the config describes them.
- Message types are matched by name. A type that is renamed and moved shows as removed plus added.
- Languages: Rust and TypeScript / JavaScript.
