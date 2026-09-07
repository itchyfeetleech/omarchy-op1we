# Token-Efficient Codex Project Workflow

This folder contains a practical workflow for taking a new software project from an idea to a tested, reviewable result while controlling Codex token and credit usage.

The recommended default is:

1. Write the original requirements in `PROJECT.md`.
2. Use GPT-6 Astra once to resolve architecture and write `PLAN.md`.
3. Use GPT-5.6 Sol for implementation, testing, debugging, documentation, and packaging.
4. Use Astra for reviews at meaningful milestones and record findings in `REVIEW.md`.
5. Return to Sol for straightforward corrections and final verification.

For a small, well-defined project, skip the separate planning session: use Sol to build it, Astra to review it once, and Sol to fix confirmed findings.

## Why this saves credits

Astra is most valuable when a decision requires stronger reasoning or judgment. Sol is substantially cheaper per token and is capable of performing most implementation work. The split saves credits only when the handoffs remain concise and each model has a clear responsibility.

Use repository files and Git commits as handoffs. Avoid transferring entire chat transcripts or asking each model to rediscover the project. Keep Fast mode off when cost matters, use Low reasoning for clear work, and use Medium reasoning for ordinary design and implementation.

Subagents and Ultra mode can improve speed or coverage on suitable projects, but each agent performs its own model and tool work. Avoid them when minimizing tokens is the priority.

## Files in this folder

- `PROJECT.md` records the product goal, requirements, constraints, and definition of done.
- `PLAN.md` records the architecture, milestones, important decisions, risks, and verification commands.
- `REVIEW.md` records actionable review findings and their resolution.

Copy all four files into a new repository. Replace the instructional text in the templates as the project develops.

## 1. Start a repository

```bash
mkdir my-project
cd my-project
git init
cp "/path/to/Project Structure/"*.md .
```

Fill in `PROJECT.md` before asking an agent to plan or write code. Describe observable behaviour and what qualifies as finished. Avoid prescribing implementation details unless they are real constraints.

Commit the initial brief:

```bash
git add PROJECT.md
git commit -m "Define project requirements"
```

## 2. Ask Astra to create the plan

Start a clean Astra session in Standard mode:

```bash
codex -m gpt-6-astra -c 'model_reasoning_effort="medium"'
```

Prompt:

```text
Read PROJECT.md and inspect the current repository.

Design the smallest reliable implementation that satisfies the brief. Resolve
the important architectural choices before implementation begins.

Complete PLAN.md with:
- chosen architecture and why;
- directory and module structure;
- data model and important interfaces;
- implementation milestones in dependency order;
- acceptance criteria for each milestone;
- testing strategy;
- material risks and assumptions;
- exact commands for running, testing and packaging the project.

Keep the plan concise enough for another coding agent to follow without this
conversation. Do not implement the application yet. Ask me only about a
decision that would materially change the architecture; otherwise make a
reasonable choice and record it.
```

Check that the plan matches the intended product, excludes optional infrastructure, and gives every milestone measurable acceptance criteria. Then commit it:

```bash
git add PROJECT.md PLAN.md
git commit -m "Define implementation plan"
```

Exit this Codex conversation so the implementation starts with a clean context.

## 3. Let Sol implement one milestone at a time

Start Sol:

```bash
codex -m gpt-5.6-sol -c 'model_reasoning_effort="medium"'
```

Prompt:

```text
Read PROJECT.md and PLAN.md.

Implement milestone 1 completely. Follow the recorded architecture unless
repository evidence shows that a change is necessary.

Before finishing:
- run the milestone's relevant tests and checks;
- fix failures caused by the implementation;
- update PLAN.md with completed items and material decisions;
- summarize changed files, test results and remaining work.

Keep the implementation focused on milestone 1. Do not begin optional work or
later milestones.
```

Review the change in Codex with `/diff`, then commit it:

```bash
git add -A
git commit -m "Implement milestone 1"
```

For another milestone in the same area, continue in the Sol session:

```text
Continue with the next incomplete milestone in PLAN.md. Implement it completely,
run the relevant checks, and update PLAN.md. Stop after that milestone.
```

Keeping related implementation and debugging in one Sol session avoids repeated repository exploration.

## 4. Use Astra at meaningful checkpoints

Astra review is most useful after the central architecture works, after security-sensitive or complex behaviour is added, or when an end-to-end version is ready. It is usually unnecessary after every small commit.

Commit the implementation, note the commit before the work being reviewed, and start a fresh Astra session:

```bash
git log --oneline
codex -m gpt-6-astra -c 'model_reasoning_effort="medium"'
```

Prompt:

```text
Read PROJECT.md and PLAN.md. Review the implementation in the Git diff from
<BASE-COMMIT> to HEAD.

Check specifically for:
- violations of the project requirements;
- incorrect architectural assumptions;
- data loss or security risks;
- important edge cases;
- tests that could pass while behaviour remains wrong;
- unnecessary complexity that materially affects maintenance.

Run focused checks when needed to confirm a suspected issue.

Update REVIEW.md with actionable findings only. For each finding include its
severity, evidence, affected files, expected behaviour and a concise suggested
fix. If there are no material findings, say so. Do not modify the implementation.
```

Replace `<BASE-COMMIT>` with an actual commit ID from `git log`, for example `a83c2e1`.

## 5. Have Sol address confirmed findings

Start or return to a Sol session:

```text
Read PROJECT.md, PLAN.md and REVIEW.md.

Validate each review finding against the current code. Fix confirmed findings
in severity order. Do not implement speculative suggestions that do not affect
the stated requirements.

Run the relevant tests after the fixes. Update REVIEW.md to record which
findings were fixed, rejected or deferred, with a brief reason.
```

Commit the fixes:

```bash
git add -A
git commit -m "Address review findings"
```

## 6. Finish and verify with Sol

Prompt:

```text
Complete the remaining items in PLAN.md required by PROJECT.md.

Then perform a clean-project verification:
- install using the documented procedure;
- run the full relevant test suite;
- run linting or type checks configured by the project;
- exercise the principal user workflow;
- verify packaging or startup instructions;
- update README.md with concise Linux installation and usage instructions.

Do not add optional features. Record the exact verification results in PLAN.md.
```

Run the project in a clean virtual environment, container, or equivalent method specified in `PLAN.md`. This catches undeclared dependencies and setup instructions that work only on the development machine.

## 7. Ask Astra for the final release review

Start a clean Astra session:

```text
Perform a final release-readiness review of this repository against PROJECT.md.

Read PLAN.md, README.md, REVIEW.md, the current implementation and recent Git
history. Run only checks needed to verify material risks.

Determine whether every completion criterion is satisfied. Look for correctness,
security, data-loss, installation and operational failures that would prevent
release.

If you find a material issue, add it to REVIEW.md with evidence and a concrete
acceptance test. Avoid style-only comments and optional enhancements. If the
project is ready, state that clearly and list the evidence.
```

Use Sol for straightforward fixes, then run the final verification once more.

## When to change the model

| Work | Suggested model | Suggested effort |
|---|---|---|
| Clear, repetitive modification | GPT-5.6 Terra | Low |
| Ordinary implementation and debugging | GPT-5.6 Sol | Medium |
| Ambiguous architecture | GPT-6 Astra | Medium |
| Difficult diagnosis or consequential decision | GPT-6 Astra | Medium, then increase only if needed |
| Milestone or final review | GPT-6 Astra | Medium |
| Documentation and packaging | GPT-5.6 Sol or Terra | Low or Medium |

If Sol fails repeatedly without producing new evidence, move the difficult diagnosis to Astra. If Astra resolves the underlying issue, let Sol apply routine follow-up changes.

## Practical token rules

1. Keep `PROJECT.md`, `PLAN.md`, and `REVIEW.md` concise and current.
2. Use a new Astra session for planning and each major review.
3. Review commits or bounded diffs rather than requesting a repository-wide audit.
4. Keep related coding and debugging in the same Sol session.
5. Request material findings rather than style suggestions.
6. Keep Fast mode off when cost matters.
7. Avoid large global `AGENTS.md` files and unused MCP servers.
8. Use `/compact` when a productive session becomes long.
9. Use `/new` when starting unrelated work.
10. Use `/status` and judge usage across the entire completed task, including rework.

