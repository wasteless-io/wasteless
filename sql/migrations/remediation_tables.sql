-- ============================================
-- Migration : Auto-Remediation Tables
-- ============================================

-- Table: actions_log
-- Tracks every action taken (stop, start, etc.)
CREATE TABLE IF NOT EXISTS actions_log (
    id SERIAL PRIMARY KEY,
    action_date TIMESTAMP DEFAULT NOW(),
    recommendation_id INTEGER REFERENCES recommendations(id),
    resource_id VARCHAR(200) NOT NULL,
    resource_type VARCHAR(50) NOT NULL,
    action_type VARCHAR(50) NOT NULL,  -- 'stop', 'start', 'terminate', 'resize'
    action_status VARCHAR(20) NOT NULL,  -- 'pending', 'success', 'failed', 'rolled_back'
    dry_run BOOLEAN DEFAULT true,
    metadata JSONB,
    error_message TEXT,
    executed_by VARCHAR(100),  -- 'system' or user email
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_actions_log_resource ON actions_log(resource_id);
CREATE INDEX idx_actions_log_date ON actions_log(action_date);
CREATE INDEX idx_actions_log_status ON actions_log(action_status);

-- Table: savings_realized
-- Tracks actual savings after remediation
CREATE TABLE IF NOT EXISTS savings_realized (
    id SERIAL PRIMARY KEY,
    recommendation_id INTEGER REFERENCES recommendations(id),
    action_log_id INTEGER REFERENCES actions_log(id),
    resource_id VARCHAR(200) NOT NULL,
    resource_type VARCHAR(50) NOT NULL,
    
    -- Period tracking
    measurement_start_date DATE NOT NULL,
    measurement_end_date DATE NOT NULL,
    
    -- Costs
    cost_before_eur DECIMAL(12, 4),  -- Average monthly cost before
    cost_after_eur DECIMAL(12, 4),   -- Average monthly cost after
    actual_savings_eur DECIMAL(12, 4),  -- cost_before - cost_after
    
    -- Comparison to estimate
    estimated_savings_eur DECIMAL(12, 4),  -- What we predicted
    savings_accuracy_percent DECIMAL(5, 2),  -- actual/estimated * 100
    
    -- Verification
    verification_method VARCHAR(50),  -- 'aws_billing', 'cost_explorer', 'manual'
    verified_at TIMESTAMP,
    verified_by VARCHAR(100),
    
    -- Metadata
    notes TEXT,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_savings_realized_resource ON savings_realized(resource_id);
CREATE INDEX idx_savings_realized_dates ON savings_realized(measurement_start_date, measurement_end_date);

-- Table: rollback_snapshots
-- Stores instance state before action for rollback
CREATE TABLE IF NOT EXISTS rollback_snapshots (
    id SERIAL PRIMARY KEY,
    action_log_id INTEGER REFERENCES actions_log(id),
    resource_id VARCHAR(200) NOT NULL,
    resource_type VARCHAR(50) NOT NULL,
    
    -- State before action
    state_before JSONB NOT NULL,  -- Full instance details
    
    -- Rollback info
    can_rollback BOOLEAN DEFAULT true,
    rollback_expiry TIMESTAMP,  -- After this, can't rollback
    rollback_executed BOOLEAN DEFAULT false,
    rollback_date TIMESTAMP,
    
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_rollback_snapshots_action ON rollback_snapshots(action_log_id);
CREATE INDEX idx_rollback_snapshots_resource ON rollback_snapshots(resource_id);

-- Comments
COMMENT ON TABLE actions_log IS 'Log of all remediation actions taken';
COMMENT ON TABLE savings_realized IS 'Verified savings after remediation';
COMMENT ON TABLE rollback_snapshots IS 'Snapshots for rolling back actions';

-- Success message
DO $$
BEGIN
    RAISE NOTICE '✅ Migration completed successfully';
    RAISE NOTICE '   - Table actions_log created';
    RAISE NOTICE '   - Table savings_realized created';
    RAISE NOTICE '   - Table rollback_snapshots created';
END $$;