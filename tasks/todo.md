# ClearDraft — task board

Deadline: Tue 22 Sep 2026. Read `PLAN.md` first, then your own brief in
`docs/`.

## Phase 0 — scaffold  (Ee Zhan)  DONE

- [x] `.gitignore` committed first; `git check-ignore` confirms `.secrets/` excluded
- [x] `core/types.py` frozen contract
- [x] Stub every module with its real signature and an owner
- [x] `adapters/inbox.py` — 520 emails load
- [x] `core/pipeline.py` — orchestrator with injected stages
- [x] `scripts/run_pipeline.py` — writes a valid `submission.json`
- [x] `eval/score.py` — organiser scorer wrapper, ground truth stays gitignored
- [x] `tests/test_contract.py` — 7 tests, all passing
- [x] Baseline measured: **0.0124** with every stage stubbed
- [x] `docs/ARCHITECTURE.md`, `docs/RUBRIC_MAP.md`, both briefs
- [x] README.md with setup instructions (mandatory per rules line 158)
- [x] Public repo live: https://github.com/TayEeZhan/cleardraft
- [x] Collaborator invitations sent to shengkuan06 and chessoreo

## Phase 1 — parallel build  (Mon 00:00-12:00)

### Sheng Kuan
- [x] `core/parsers/txt.py`
- [x] `core/parsers/xlsx.py`
- [x] `core/parsers/docx.py`
- [x] `core/parsers/pdf.py`
- [x] Alias table extended beyond the generator's labels (PR #3)
- [x] `core/extract.py` with the verification gate

### Zi Qi
- [x] `core/classify.py` rule tier (Zi Qi, PR #1)
- [x] Model fallback through `adapters/model.py` (evidence-gated)
- [x] Dev slice, confusion matrix, macro-F1, regression test (`eval/dev.py`, `eval/metrics.py`, `tests/test_eval.py`)
- [x] Unit tests for `core/normalise.py` (`tests/test_normalise.py`)

### Ee Zhan
- [x] `core/normalise.py`
- [x] `core/compare.py`
- [x] `core/decide.py` precedence ladder
- [x] `core/reply.py`
- [x] `adapters/model.py` client

**Validated 2026-09-20:** a throwaway parser feeding the real
normalise/compare/decide path scored **84/84 exact defect-field match** on
every comparable .txt pair. The comparison engine is not the risk any more;
extraction and classification are.

## Phase 2 — integration  (Mon 12:00-16:00)
- [x] Merge Zi Qi PR #1
- [x] First full scoring run
- [x] Target: end-to-end rate above 0.90 -> reached 1.00

## Phase 3 — product  (Mon 16:00-22:00)
- [x] FastAPI service: /api/health, /api/check (Vercel Python function)
- [x] Web UI (static HTML/CSS/JS): board, review, accuracy, live checker
- [x] Deploy to Vercel: https://cleardraft-one.vercel.app
- [x] Home menu, printable check report, inbox search (2026-09-21)
- [ ] `docker-compose.yml` for reproducibility (after prelims)

## Phase 4 — review  (optional; after the submission is in)
- [ ] `ecc:silent-failure-hunter` over the whole repository
- [ ] `ecc:python-reviewer` over `core/`
- [ ] `ecc:fastapi-reviewer` over `api/`
- [ ] `ecc:react-reviewer` over `web/`
- [ ] `ecc:security-reviewer` before the repository goes public
- [ ] `ecc:e2e-runner` against the deployed URL
- [ ] `/code-review high` over the full diff

## Phase 5 — submission  (Tue morning)
- [ ] Slide deck, one artefact per rubric row — see `docs/RUBRIC_MAP.md`
- [ ] Five-minute demo video, run against the live URL
- [x] Project description text: `docs/PROJECT_DESCRIPTION.md` (still to paste into the submission form)
- [x] Prototype link live: https://cleardraft-one.vercel.app (`/api/health` 200, `/api/check` live)
- [x] Public repository, ground-truth labels excluded (checked: no `.env`, `.secrets/` or labels in any commit)


---

## Measured result, 2026-09-20

Full 520-email run against the organiser's own scorer:

```
FINAL SCORE            1.0000
  stage1 macro F1      1.0000   (weight 0.30)
  stage3 defect F1     1.0000   (weight 0.20)
  end-to-end rate      1.0000   (weight 0.50)   46/46 defects caught
  escalation recall    1.0000
  escalation precision 0.9091
  decided by rule      100.0%
```

**Read this honestly.** It is a validation number on the one corpus we hold
labels for, not evidence of generalisation. Two specific reasons to distrust it
as a predictor of the final round:

1. The classifier's rules match the generator's literal body templates. They
   score 1.00 here by construction and will not transfer to rephrased email.
   They decline rather than guess, so unmatched mail falls through to the model
   tier - which is untested, because no API key has been configured yet.
2. The parsers were built against these four renderers. The alias table covers
   the labels this generator emits and little else.

Escalation precision 0.9091 is not a defect: two SI PDFs have the notify-party
label physically overlapping its value, so we escalate rather than compare
garbage. Gold marks them OK because the underlying data matches - but we
genuinely cannot read it.

### What actually moves the number on unseen data
- Alias coverage beyond this generator  (Sheng Kuan)
- Intent-level classifier rules, not template matches  (Zi Qi)
- An API key, so the model tier is exercised at all  (Ee Zhan)

---

## Final round build — 24 Sep 2026 (Opus plans/reviews, Sonnet builds)

Rubric weight: Technical 70 (E2E 25, Architecture 15, Tech integration 15,
Robustness 15), Product 30 (Value 10, UX + differentiation 10, Impact 10).

- [x] WP-A (Sonnet, worktree): inbox triage banner "N need you now" + Start +
      progress + green done state with time saved + "k left" per tab
- [x] WP-B (Sonnet, worktree): per-kind confirm labels; `d` blocked on
      Needs-a-human; Undo toast; source lines open on mismatches; roll into
      Needs-a-human when discrepancies are done; "Reply sent — next case"
- [x] WP-C (Sonnet, worktree): container count sums all "N x TYPE" groups (bug:
      mixed groups passed as match); model client timeout; /api/health commit +
      model + switch; GitHub Actions CI + badge
- [x] Opus review of A, B, C diffs (all MERGE AFTER FIXES; fixes applied)
- [x] Merged on local branch final/ux-robustness: 290 passed, 1 skipped;
      score 46/46, end-to-end 1.0000; browser check of banner, Start, d, Undo,
      Needs-a-human guard
- [x] Upload a dataset (.zip): /api/process-dataset + third inbox source;
      security review fixes (confined attachment paths, per-request model
      budget, 400 on bad zips); 520 emails in ~6 s locally
- [x] Mark-as-same interplay fixes (review bar + banner use re-counted status)
- [x] .gitattributes: PDFs binary (Windows clones corrupted email_499_BL.pdf)
- [x] E2E fresh-visitor test, desktop + 375 px + 768 px: all PASS; mobile
      triage gap fixed
- [x] Push to main

### Review (24 Sep)
400 passed, 1 skipped; organiser score 1.0000 (46/46). Opus reviewed every
Sonnet diff; every review found real issues, all fixed before merge.
- [ ] DECIDE (user): model-read match -> NEEDS_REVIEW (closes wrong-field gap)
- [x] Spend cap: not wanted (user, 24 Sep)
- [x] Held-out re-run: 21 calls, 0 wrong; page shows 18 of 41
- [x] PR #5 was closed by its author; SK PRs #6/#7 + 3 docs commits merged
