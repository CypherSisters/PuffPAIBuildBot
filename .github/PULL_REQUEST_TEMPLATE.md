## What this changes

<!-- Short description of the change. -->

## Capability matrix rows touched

<!-- List the row ID(s) this PR affects, e.g. BB-SRC-06, BB-POS-04.
     If it doesn't touch a matrix row, say "none". -->

## Test evidence

<!-- Paste the relevant pytest summary line(s) before/after, and name any
     new tests added. -->

```
paste test output here
```

## Checklist

- [ ] `py_compile` passes on every file touched
- [ ] No new failing test names vs. the pre-change baseline
- [ ] No new external hosts introduced (no gate removed, allowlist
      widened, outbound host added, secret logged, or PII tier lowered
      without calling it out explicitly below)
