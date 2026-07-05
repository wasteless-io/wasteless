-- Terraform PR remediation (GitOps mode): a routed recommendation gets
-- status 'pr_open' and carries the PR URL until the scheduler sees the
-- PR merged (-> approved) or closed (-> rejected).
ALTER TABLE recommendations
    ADD COLUMN IF NOT EXISTS pr_url TEXT;
