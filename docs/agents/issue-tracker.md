# Issue tracker: GitHub

Issues and specs live in this repository's GitHub Issues.
Use the `gh` CLI; infer the repository from the Git remote.

## Operations

- Create: `gh issue create --title "..." --body-file <file>`
- Read: `gh issue view <number> --comments`
- List: `gh issue list --state open --json number,title,body,labels`
- Comment: `gh issue comment <number> --body-file <file>`
- Label: `gh issue edit <number> --add-label "..." --remove-label "..."`
- Close: `gh issue close <number> --comment "..."`

For multiline bodies, write the text to a temporary file and use
`--body-file`.

"Publish to the issue tracker" means create a GitHub issue.
"Fetch the relevant ticket" means read the issue and its comments.

## Pull requests as a triage surface

**PRs as a request surface: no.**

## Wayfinding

Use one issue labelled `wayfinder:map` for Notes, Decisions-so-far,
and Fog. Link child tickets using GitHub sub-issues, or a task list
with `Part of #<map>` in each child when sub-issues are unavailable.

Label children `wayfinder:<type>`: research, prototype, grilling,
or task. Use native issue dependencies for blockers, falling back
to a `Blocked by: #<number>` line when unavailable.

The next ticket is the first open, unassigned child in map order
with every blocker closed. Claim it by assigning yourself.
On resolution, comment with the answer, close the ticket, and
append a summary and link to the map's Decisions-so-far.
