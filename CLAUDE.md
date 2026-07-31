# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Directory Is

`fusion-projects` is a **workspace meta-directory** within the Fusion monorepo (`~/fusion/`). It does not contain its own source code — it's a Claude Code working context for tasks that span multiple Fusion projects. Each sibling directory is an independent project with its own CLAUDE.md, venv, and build system.

## Fusion Ecosystem Overview

Fusion is a local-first AI product matrix for Apple Silicon. One inference base, three capability sub-projects, one unified desktop shell:

```
fusion-mlx (inference base, Python/MLX/Metal, port 11434)
    ↑ HTTP OpenAI-compatible API
    ├── fusion-agent-studio  (Agent orchestration, DAG workflows, Python)
    ├── fusion-code          (AI coding CLI, TypeScript/Bun)
    ├── fusion-multi-node    (distributed cluster scheduling, Python)
    └── fusion-studio        (unified macOS desktop, SwiftUI/Rust/Python)
```

Additional projects: `fusion-cowork` (automation), `fusion-design` (visual design, Rust), `fusion-security` (code audit), `fusion-k12-teacher` (education), `fusion-rag`/`fusion-kb` (knowledge base), `fusion-model-hub`, `fusion-cli`, `fusion-bench`, `fusion-simulation`, `fusion-comfyui`, `fusion-trainer`, `fusion-finance`, `fusion-health`, `fusion-science`, `fusion-gateway`, `fusion-artifacts-engine`, `fusion-plugins-ecosystem`, `fusion-doc`, `MlxGEMM`.

## Key Conventions Across All Projects

- **fusion-mlx**: Start/stop with `~/claude-home/fusion-mlx/start.sh start|stop`. Model downloads via `https://hf-mirror.com`
- **Python projects**: Always `source .venv/bin/activate` first. Install with `pip install -e ".[test]"`
- **4-space indentation** (multiples of 4) everywhere — Python, Swift, Rust, TypeScript
- **No docstrings** in code
- **Logging required** in all modules — `logger = logging.getLogger(__name__)` (Python), `tracing::*!` (Rust), `Logger(subsystem:)` (Swift)
- **All LLM calls go through fusion-mlx HTTP API** — no direct MLX/torch imports in upper-layer projects
- **Shared `fusion-core`** (`~/fusion/fusion-core/`): provides `FusionMLXClient` used by Python projects

## Communication Protocols

| Channel | Protocol | Usage |
|---------|----------|-------|
| fusion-studio ↔ agent-studio | UDS + JSON-RPC 2.0 (`/tmp/fusion-studio.sock`) | Graph/agent management |
| fusion-studio ↔ fusion-code | Subprocess CLI (stdin/stdout) | Code generation |
| All projects → fusion-mlx | HTTP `localhost:11434/v1` | LLM inference |
| agent-studio → multi-node | Python import | Cluster inference |
| fusion-rag | HTTP `localhost:11436` | Knowledge base API |

## Key Ports

| Port | Service |
|------|---------|
| 11434 | fusion-mlx (OpenAI-compatible API) |
| 11436 | fusion-rag (knowledge base) |
| 9753-9755 | fusion-multi-node (cluster) |

## Cross-Project Rules

- **Only modify the target project's code** — upstream issues require: file issue → submit PR → follow through to merge
- **Bug fixes must trace all failing tests** to root cause, even if unrelated to your change
- **AI/LLM tests require real model loading** — no mocks for integration tests
- See `~/fusion/docs/PROJECT_RELATIONSHIPS.md` for the full architecture diagram and data flow details
