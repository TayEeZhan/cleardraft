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
- [ ] `core/parsers/txt.py`
- [ ] `core/parsers/xlsx.py`
- [ ] `core/parsers/docx.py`
- [ ] `core/parsers/pdf.py`
- [ ] Alias table extended beyond the generator's labels
- [ ] `core/extract.py` with the verification gate

### Zi Qi — see `docs/BRIEF_ZI_QI.md`
- [ ] `core/classify.py` rule tier
- [ ] Model fallback through `adapters/model.py`
- [ ] Dev slice, confusion matrix, macro-F1, regression test
- [ ] Unit tests for `core/normalise.py`

### Ee Zhan
- [x] `core/normalise.py`
- [x] `core/compare.py`
- [x] `core/decide.py` precedence ladder
- [x] `core/reply.py`
- [ ] `adapters/model.py` client

**Validated 2026-09-20:** a throwaway parser feeding the real
normalise/compare/decide path scored **84/84 exact defect-field match** on
every comparable .txt pair. The comparison engine is not the risk any more;
extraction and classification are.

## Phase 2 — integration  (Mon 12:00-16:00)
- [ ] Merge three branches
- [ ] First full scoring run
- [ ] Target: end-to-end rate above 0.90

## Phase 3 — product  (Mon 16:00-22:00)
- [ ] FastAPI service
- [ ] Next.js UI: board, review, accuracy
- [ ] Deploy to Vercel and Render
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
