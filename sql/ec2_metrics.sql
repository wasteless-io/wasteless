-- ============================================
-- Migration 001: Add EC2 Metrics Table
-- Description: Store CloudWatch metrics for EC2 instances
-- Author: Wasteless
-- Date: 2025-01-11
-- ============================================

-- Create ec2_metrics table
CREATE TABLE IF NOT EXISTS ec2_metrics (
    id SERIAL PRIMARY KEY,
    instance_id VARCHAR(50) NOT NULL,
    instance_type VARCHAR(50),
    instance_name VARCHAR(200),
    instance_state VARCHAR(20),
    collection_date DATE NOT NULL,
    cpu_avg DECIMAL(5, 2),
    cpu_max DECIMAL(5, 2),
    cpu_p95 DECIMAL(5, 2),
    network_in_mb DECIMAL(12, 2),
    network_out_mb DECIMAL(12, 2),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_ec2_metrics_instance ON ec2_metrics(instance_id);
CREATE INDEX IF NOT EXISTS idx_ec2_metrics_date ON ec2_metrics(collection_date);
CREATE INDEX IF NOT EXISTS idx_ec2_metrics_instance_date ON ec2_metrics(instance_id, collection_date);

-- Create unique constraint to prevent duplicates
CREATE UNIQUE INDEX IF NOT EXISTS ec2_metrics_instance_date_unique
    ON ec2_metrics(instance_id, collection_date);

-- Add comments for documentation
COMMENT ON TABLE ec2_metrics IS 'CloudWatch metrics for EC2 instances over time';
COMMENT ON COLUMN ec2_metrics.instance_id IS 'AWS EC2 instance ID (e.g., i-0abc123...)';
COMMENT ON COLUMN ec2_metrics.instance_name IS 'Instance Name tag value';
COMMENT ON COLUMN ec2_metrics.collection_date IS 'Date when metrics were collected';
COMMENT ON COLUMN ec2_metrics.cpu_avg IS 'Average CPU utilization over 24 hours (0-100)';
COMMENT ON COLUMN ec2_metrics.cpu_max IS 'Maximum CPU utilization over 24 hours (0-100)';
COMMENT ON COLUMN ec2_metrics.cpu_p95 IS 'P95 CPU utilization - Reserved for future percentile-based analytics';
COMMENT ON COLUMN ec2_metrics.network_in_mb IS 'Average network inbound traffic in MB';
COMMENT ON COLUMN ec2_metrics.network_out_mb IS 'Average network outbound traffic in MB';

-- Create update trigger
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_ec2_metrics_updated_at
    BEFORE UPDATE ON ec2_metrics
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Success message
DO $$
BEGIN
    RAISE NOTICE '✅ Migration 001 completed successfully';
    RAISE NOTICE '   - Table ec2_metrics created with correct column names';
    RAISE NOTICE '   - Columns: instance_id, instance_type, instance_name, instance_state';
    RAISE NOTICE '   - Columns: collection_date (not metric_date)';
    RAISE NOTICE '   - Columns: cpu_avg, cpu_max, cpu_p95 (not avg_cpu_percent)';
    RAISE NOTICE '   - Columns: network_in_mb, network_out_mb (DECIMAL(12,2))';
    RAISE NOTICE '   - 4 indexes created (including unique constraint)';
    RAISE NOTICE '   - Update trigger created';
END $$;
