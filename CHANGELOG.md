---
doc_id: fusion-projects/changelog
product: fusion-projects
class: app
entry: service
doc_type: changelog
title: { zh: "变更日志", en: "Changelog" }
audience: engineer
status: draft
version: 0.7.9
updated: 2026-11-07
owner: "@dahai"
source_of_truth:
  - CHANGELOG.md
---

# Changelog

All notable changes to fusion-projects are documented here.

## Unreleased

## 0.7.9 — 2026-11-26

### Lint

- `ruff check .` now fully green with zero suppressions (PR #43): the last UP042 residual fixed manually — `PromptMergeMode(str, Enum)` → `StrEnum` in `project_service/models/agent_binding.py`. Equivalence verified: all consumers use `.value` for persistence/equality, no `str(member)` interpolation anywhere, Pydantic v2 validation/serialization identical, plain-string construction unchanged. 206 tests green.

## 0.7.8 — 2026-11-26

### Lint

- Add `[tool.ruff]` gate (line-length 120, select E/F/W/I/UP/B; B008 ignored for the FastAPI `Depends()` idiom, E501 deferred) and `ruff check .` green — closes #40/#41 (PR #42). Stockpile cleared: `Optional[X]` to `X | None` (247), import sorting, unused imports, `typing.Dict/List/Tuple` to builtins, `collections.abc` imports, f-strings, `datetime.UTC`, 76 `raise ... from` chains (AST-assisted). 1 residual UP042 (StrEnum migration changes `str()` semantics) left intentionally. 214 tests collected before == after.

## 0.7.7 — 2026-11-16

### CI

- Retrofit per fusion#2 checklist (closes #38, PR #39): `concurrency` + `cancel-in-progress` grouped by ref on `ci.yml` and `docs.yml`; pip dependency cache (`setup-python` `cache: pip`) keyed on `pyproject.toml` in test and build jobs. Sharding and nightly tiered gate assessed as not warranted (~50s suite wall time).

## 0.7.6 — 2026-11-07

### Docs

- Adopt the fusion docs-system v1.1 contract (app x service): added `docs/product-page.md`, `docs/get-started.md`, `docs/api-reference.md`, `docs/config-reference.md`, `docs/troubleshooting.md`; added 12-key frontmatter to all in-scope markdown files; added `ci/validate_docs.py` and `.github/workflows/docs.yml`.
