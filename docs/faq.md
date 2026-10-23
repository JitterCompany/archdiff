# FAQ

## Idea

### Why review architecture instead of lines?

When an LLM writes most of the code, reading every line stops paying off. What a reviewer still has to judge is what the change does to the system: which parts it touches, which dependencies it adds or drops, which interfaces and message types change. archdiff extracts exactly that and shows it, so the line-level review can focus where the structure moved.

### Why generate the diagram instead of drawing one?

A hand-drawn architecture diagram goes stale within weeks. archdiff reads the structure from the code at both revisions, so the picture is always the one the PR actually produces.

### Does it replace the code review?

No. It tells you where to look and what kind of change it is. "No structural changes, internal edits only" is a useful answer too: then a line review of that one module is enough.

## Code view

### How does it pick the level of detail for a PR?

The tree has fixed levels: L0 system, L1 container (a crate, an app area), L2 module, then units. archdiff starts with all systems and keeps expanding the node that reveals the most new structure: dependencies that appear or vanish first, then contract changes, then churn. It stops at a node budget (18 by default, adjustable). Unchanged siblings collapse into one "N more" node.

The headline names the coarsest level where a dependency appeared or vanished, e.g. "Dependency changes at L1 container".

### What happens when I click a block?

Click zooms in: the block fills the canvas with its own children and the wiring between them, changed or not. Outside it, only the blocks it has dependencies with are drawn, each at the level where it branches off. The breadcrumb above the graph and the browser's back button go up again. The zoom level is in the URL (`?focus=...`), so you can link someone to it.

- Shift-click opens a block in place instead, to see two neighbours side by side.
- Clicking a line under "Structural change" in the headline zooms to where that change happens.
- "Whole system" goes back to the top.

### What does a "structural change" mean?

A dependency between two units that exists on one side only. Edges are compared after aggregating to the level shown, so a file that switches imports inside the same module is not a structural change at module level.

### A module shows a child named after a file, like `reports.rs` or `lib.rs`. Why?

That unit is both a file and a parent: `reports.rs` next to a `reports/` folder, or a crate's `lib.rs`. When you expand it, its own code needs a place to live, so it gets a child named after the file.

### Moved files show up as moved, not as deleted plus added. How?

archdiff uses git's rename detection and expresses the base revision in head paths before comparing. Only dependencies that really changed remain. Crates that move into a git dependency (outside the repo) are matched by module names, because rename detection stops at the repo boundary.

### How do I see changes in a git dependency, like a shared library?

Git dependencies in `Cargo.toml` become their own top-level node, showing the rev change. archdiff diffs the dependency between the two revs (from cargo's cache, or its own clone) and reports how many of the items your code imports have a changed definition, and which new items you start using. Inside the dependency it shows only churn per module, not its internal dependencies.

### What is the "Mermaid source" button for?

It shows the Mermaid text for the current view. Paste it into an issue, a PR comment or a markdown doc and it renders there. It is also handy to tell whether odd output comes from the data or from the layout.

## System view

### What is the system view?

The running system instead of the code: tasks, the queues or channels between them, and the message type on each queue. A change shows up as a new or removed task, a re-wired connection, a capacity change, or a change in a message type (variants or fields added, removed or changed). Many structural PRs leave the code dependencies alone and change only what flows over a queue; this view catches those.

### Which frameworks does it understand?

- tokio-style servers: an entry file that creates `mpsc::channel(N)` pairs and starts tasks with `start_task(...)`.
- RTIC 2 firmware: `#[task]`, `#[init]`, `#[idle]`, priorities, interrupt bindings, shared resources, channel ends passed through `Local` / `Shared` or `spawn(...)`, and the spawn tree.

Projects describe their conventions in `[system_view]`, for example the macros that generate channel constructors.

### Can I zoom into a task?

Not yet. For RTIC the spawn tree already shows tasks that spawn other tasks. For tokio, tasks that create their own queues internally are a planned next step. "Show in code view" in the task details jumps to the task's module.

### Why do some tasks or queues not show?

Tasks and queues with no visible connection and no change are hidden. Tick "unconnected" to see them. Interrupt handlers are linked to the tasks they wake where the peripheral can be traced (see the README); a handler for something every task uses, like the system timer, stays unconnected.

## Setup and hosting

### Does it work for any project?

The engine is generic; project knowledge lives in `archdiff.toml`. Rust and TypeScript / JavaScript are supported. The system view needs a front-end per framework (tokio and RTIC today).

### Do I need to host the pages?

Not strictly. The PR comment works on its own and contains the summary. The interactive page needs a place to live, and it should be one click away or nobody opens it.

### Why not GitHub Pages?

On most plans, Pages sites built from private repos are public. The page shows the internal architecture of your code, so it must sit behind a login. Access-controlled Pages needs GitHub Enterprise.

### Why does CI generate the page and not the server?

The repo credentials stay in GitHub secrets and the server stays dumb. If the server generated the pages, it would need read access to every analysed repo on an internet-facing machine.

### Can the server host the tool so CI downloads it from there?

Better not. CI would then run code from that server with your CI secrets in the environment, and every repo would always get the latest version. Pinning a tag of this repo in `uses:` gives version control and review for free.

### Is the OAuth "callback URL" the same as the "redirect URI"?

Yes. Use exactly `https://<domain>/oauth2/callback` and leave wildcard matching off.

### Which org goes in `OAUTH2_PROXY_GITHUB_ORG`?

The org whose members may view the pages, usually the org that owns the analysed repos. It can differ from the org that owns the OAuth app.

### Login works but I get a 500 afterwards.

Almost always org access restrictions: the app is not yet granted in that org. See [setup step 2d](setup.md#2d-org-access-restrictions).

### Do I run `gh secret set` inside the repo?

No. With `-R owner/repo` it works from any directory.

### Is the client ID secret?

No. The client secret and the cookie secret are. Keep them only in `/etc/archdiff/oauth2-proxy.env` on the host.
