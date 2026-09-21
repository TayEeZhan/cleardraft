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

### Sheng Kuan — see `docs/BRIEF_SHENG_KUAN.md`
- [x] `core/parsers/txt.py`
- [x] `core/parsers/xlsx.py`
- [x] `core/parsers/docx.py`
- [x] `core/parsers/pdf.py`
- [ ] Alias table extended beyond the generator's labels
- [x] `core/extract.py` with the verification gate

### Zi Qi — see `docs/BRIEF_ZI_QI.md`
- [x] `core/classify.py` rule tier (Zi Qi, PR #1)
- [ ] Model fallback through `adapters/model.py`
- [ ] Dev slice, confusion matrix, macro-F1, regression test
- [ ] Unit tests for `core/normalise.py`

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
- [ ] `docker-compose.yml` for reproducibility

## Phase 4 — review  (Mon 22:00-Tue 02:00)
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
- [ ] Project description
- [ ] Prototype link
- [ ] Public repository, ground-truth labels excluded


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
