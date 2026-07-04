-- AI-generated explanation of a recommendation (optional feature: filled
-- only when WASTELESS_LLM_MODEL is configured, see src/core/llm.py).
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS ai_insight TEXT;
