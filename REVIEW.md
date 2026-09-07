# Review Findings

Use this file for material, actionable findings from milestone and release reviews. Avoid style-only suggestions and optional redesigns.

## Review scope

- Requirements: `PROJECT.md`
- Plan: `PLAN.md`
- Diff or version reviewed: **[base commit]..[head commit]**
- Review date: **YYYY-MM-DD**
- Reviewer/model: **[model and reasoning effort]**

## Release assessment

Status: **Not reviewed | Blocked | Ready after fixes | Ready**

[One concise paragraph explaining the assessment and evidence.]

## Findings

### [F-001] [Concise finding title]

- **Severity:** Critical | High | Medium | Low
- **Status:** Open | Fixed | Rejected | Deferred
- **Affected files:** `[path]`
- **Requirement affected:** [reference to `PROJECT.md`]
- **Evidence:** [specific behaviour, code, command output, or reproducible case]
- **Expected behaviour:** [what should happen]
- **Suggested fix:** [smallest suitable correction]
- **Acceptance test:** [specific test or verification that proves resolution]
- **Resolution:** [commit, explanation, or reason for rejection/deferral]

<!-- Copy the finding section above for each additional material issue. -->

## Checks performed

| Check | Command or method | Result |
|---|---|---|
| Requirements comparison | Read `PROJECT.md` and implementation | [Result] |
| Relevant automated tests | `[command]` | [Result] |
| Focused manual verification | [method] | [Result] |

## Final disposition

- [ ] All Critical and High findings are resolved.
- [ ] Confirmed Medium findings are resolved or explicitly accepted.
- [ ] Acceptance tests for fixes pass.
- [ ] `PLAN.md` final verification is complete.
- [ ] The release assessment above is current.

