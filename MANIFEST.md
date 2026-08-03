# Manifest

## The problem

Designing an image-analysis workflow and knowing whether it is better than
the alternative are two different jobs. The first one has tools. The second
one is done by eye, and so pipeline choices are defended by impression
rather than by measurement.

## The loop

1. **Build** — assemble a workflow from nodes on a small sample dataset,
   watching what each step does. Save it as JSON. *(DIPLO)*
2. **Run** — apply that workflow to real data: schedule it, run many at
   once, keep the results. *(the Hub)*
3. **Compare** — score the results of one workflow run against another on
   the same data. *(the eval tool)*
4. **Decide** — read the comparison and pick the configuration that fits
   the use case. *(the user, or their agent)*

The Hub connects the three and remembers what happened.

## Division of labour

- **DIPLO builds workflows.** It does not run production jobs and does not
  measure. Its output is a workflow JSON.
- **The eval tool measures.** It consumes workflow *results*, not graphs,
  and is usable without DIPLO and without the Hub.
- **The Hub orchestrates.** It runs tools on data, routes each tool's
  output into the next tool's input, records what was produced, and exposes
  all of it. It contains no processing code of its own.

Step 4 is deliberately not automated. The Hub's job is to make the evidence
retrievable, not to choose for you.

## Principles

- Tools are replaceable. DIPLO is the first workflow builder, not the only
  one; the same is true of every other tool.
- Anything the web UI can do, the API and the CLI can do. Agents are
  first-class users, not an afterthought.
- Results are tracked data, not files someone has to remember the path to.
- A tool describes its settings with a schema, so the human form and the
  agent call are generated from one source of truth.
