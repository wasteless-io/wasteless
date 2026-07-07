# ui/static/

Static assets served by FastAPI's `StaticFiles` mount (`/static`, see
`ui/main.py`).

| Path | Purpose |
|---|---|
| `chart.umd.min.js` | Vendored Chart.js build — powers the Dashboard's waste trend and waste-by-resource charts. Vendored (not npm-installed) since the UI has no JS build step. |
| `images/` | Logo variants (`logo.svg`/`.png`, `logo-optimized.*`, `logo-simple.svg`) and `favicon.svg`. Multiple logo variants exist for different contexts (nav bar vs. favicon vs. landing page) — check `ui/templates/` for which template uses which. |
| `providers/` | LLM provider icons (`anthropic`, `claude`, `deepseek`, `gemini`, `mistral`, `ollama`, `openai`) shown in Settings when picking an AI insights provider (see `install.sh`'s provider choice and `src/core/llm.py`). |
