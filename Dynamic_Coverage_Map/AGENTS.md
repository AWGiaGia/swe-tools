# Repository Guidelines

## Project Structure & Module Organization

Core ingestion and orchestration scripts (`run_dockers.py`, `prepare_repos.py`, `pull_dockers.py`) live at the repo root; they orchestrate Docker execution and dataset prep. Parsing utilities such as `parse_coverage_map.py`, `parse_call_graph.py`, and `parse_call_tree.py` transform collected traces into downstream artifacts under `dynamic_scikit-learn*`. The `utils/` directory contains tracer logic injected into containers (`trace.py`, `hooks.py`). Runtime outputs go to `results/<instance>/result/`, while `logs/` and `automation_logs/` capture batch diagnostics.

## Build, Test, and Development Commands

Use **the `conda activate agentless` environment** with Python 3.8+ and Docker installed. Helpful entry points:

```bash
python generate_swebench_list.py --dataset /data/swe-bench-lite --output swebench_lite_images.txt
python prepare_repos.py --dataset /data/swe-bench-lite --target ./sklearn-swe-bench
python run_dockers.py --script-dir ./utils --result-dir ./results --log-dir ./logs
python parse_coverage_map.py --source_folder ./results --save_folder ./dynamic_scikit-learn_repaired
```

All parameters should be stored **directly inside the corresponding `.py` files via `argparse` definitions**.

## Coding Style & Naming Conventions

Follow PEP 8 with 4-space indentation, descriptive snake\_case for functions, and CONSTANT\_CASE for module-level settings (see `utils/trace.py`). Keep scripts import-light and prefer explicit CLI arguments over globals. Add short docstrings for public helpers and inline comments only where logic is not obvious. **All generated artifacts must be stored in `results*/`**, with hyphenated suffixes that match the output format (`*_call_graph`, `*_call_tree`).

## Testing Guidelines

The repo’s “tests” are trace-collection dry runs. Before opening a PR, execute one small batch to ensure Docker + pytest hooks succeed:

```bash
python run_dockers.py --max 2 --image-prefix ghcr.io/epoch-research/swe-bench.eval.x86_64
```

Validate parsing scripts with representative folders and inspect `results/<instance>/result/tests-info.json` plus `traces.json` for schema regressions. New utilities should expose a `--dry-run` or `--max` flag so contributors can run bounded checks locally.

## Commit & Pull Request Guidelines

Recent history favors short, imperative commit titles (e.g., `add README.md`). Describe scope in one sentence and separate logical changes. PRs should include: purpose summary, reproduced/expected behavior, command logs for any batch runs, and links to SWE-bench issue IDs if relevant. Attach snippets from `logs/batch_run_*.log` or parsed artifacts when altering tracing semantics, and mention any data directories contributors must create.

When implementing feature requests or editing the system based on user requirements, contributors should **prefer minimal, non-intrusive modifications**. Avoid large-scale changes unless strictly necessary, as overly invasive edits can unintentionally break existing behaviors or disrupt the reliability of the processing pipeline.

## Security & Configuration Tips

Never commit raw SWE-bench datasets or proprietary Docker credentials. Store machine-specific paths in `.env.local` or pass them via CLI flags. When sharing example commands, redact instance IDs tied to private evaluations and avoid publishing full `traces.json` dumps—share derived metrics instead.