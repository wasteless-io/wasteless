#!/usr/bin/env python3
"""
Safeguards Module for Wasteless Auto-Remediation

Ensures remediation actions are safe before execution.
Multiple layers of protection.

Author: Wasteless
"""

import os
import yaml
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class SafeguardException(Exception):
    """Exception raised when safeguard check fails."""
    pass


class Safeguards:
    """Multi-layer safeguard system for auto-remediation."""
    
    def __init__(self, config_path: str = "config/remediation.yaml"):
        """Initialize safeguards with configuration."""
        self.config_path = config_path
        self.config = self._load_config()
        logger.info("Safeguards initialized")
    
    def _load_config(self) -> Dict:
        """Load configuration from YAML file."""
        try:
            with open(self.config_path, 'r') as f:
                config = yaml.safe_load(f)
            logger.info(f"Configuration loaded from {self.config_path}")
            return config
        except FileNotFoundError:
            logger.error(f"Config file not found: {self.config_path}")
            raise
        except yaml.YAMLError as e:
            logger.error(f"Invalid YAML in config: {e}")
            raise
    
    def is_auto_remediation_enabled(self) -> bool:
        """Check if auto-remediation is globally enabled."""
        enabled = self.config.get('auto_remediation', {}).get('enabled', False)
        
        if not enabled:
            logger.warning("⚠️  Auto-remediation is DISABLED (dry-run only)")
        
        return enabled
    
    def is_whitelisted(self, instance_id: str, instance_tags: Dict) -> bool:
        """
        Check if instance is whitelisted (protected).
        
        Args:
            instance_id: EC2 instance ID
            instance_tags: Instance tags dict
            
        Returns:
            True if whitelisted (DO NOT touch), False otherwise
        """
        whitelist = self.config.get('whitelist', {})
        
        # Check instance ID whitelist
        whitelisted_ids = whitelist.get('instance_ids', [])
        if instance_id in whitelisted_ids:
            logger.warning(f"🛡️  Instance {instance_id} is WHITELISTED (ID)")
            return True
        
        # Check tag-based whitelist
        whitelisted_tags = whitelist.get('tags', [])
        for tag_rule in whitelisted_tags:
            key = tag_rule.get('key')
            value = tag_rule.get('value')
            
            if instance_tags.get(key) == value:
                logger.warning(
                    f"🛡️  Instance {instance_id} is WHITELISTED "
                    f"(tag {key}={value})"
                )
                return True
        
        return False
    
    def check_instance_age(self, launch_time: datetime) -> bool:
        """
        Check if instance is old enough to be stopped.
        
        Args:
            launch_time: Instance launch datetime
            
        Returns:
            True if old enough, False otherwise
            
        Raises:
            SafeguardException: If instance too young
        """
        min_age_days = self.config.get('protection', {}).get(
            'min_instance_age_days', 30
        )
        
        age_days = (datetime.now(launch_time.tzinfo) - launch_time).days
        
        if age_days < min_age_days:
            raise SafeguardException(
                f"Instance too young: {age_days} days "
                f"(minimum: {min_age_days} days)"
            )
        
        logger.debug(f"✅ Instance age OK: {age_days} days")
        return True
    
    def check_confidence_score(self, confidence: float) -> bool:
        """
        Check if confidence score meets minimum threshold.
        
        Args:
            confidence: Confidence score (0.0-1.0)
            
        Returns:
            True if confident enough
            
        Raises:
            SafeguardException: If confidence too low
        """
        min_confidence = self.config.get('protection', {}).get(
            'min_confidence_score', 0.80
        )
        
        if confidence < min_confidence:
            raise SafeguardException(
                f"Confidence too low: {confidence:.2f} "
                f"(minimum: {min_confidence:.2f})"
            )
        
        logger.debug(f"✅ Confidence OK: {confidence:.2f}")
        return True
    
    def check_idle_duration(self, idle_days: int) -> bool:
        """
        Check if instance has been idle long enough.
        
        Args:
            idle_days: Number of days instance has been idle
            
        Returns:
            True if idle long enough
            
        Raises:
            SafeguardException: If not idle long enough
        """
        min_idle_days = self.config.get('protection', {}).get(
            'min_idle_days', 14
        )
        
        if idle_days < min_idle_days:
            raise SafeguardException(
                f"Not idle long enough: {idle_days} days "
                f"(minimum: {min_idle_days} days)"
            )
        
        logger.debug(f"✅ Idle duration OK: {idle_days} days")
        return True
    
    def check_schedule(self) -> bool:
        """
        Check if current time is within allowed schedule.
        
        Returns:
            True if within schedule
            
        Raises:
            SafeguardException: If outside allowed schedule
        """
        schedule = self.config.get('schedule', {})
        
        now = datetime.now()
        current_day = now.strftime("%A")
        current_hour = now.hour
        
        allowed_days = schedule.get('allowed_days', [])
        allowed_hours = schedule.get('allowed_hours', [])
        
        if allowed_days and current_day not in allowed_days:
            raise SafeguardException(
                f"Outside allowed schedule: {current_day} not in {allowed_days}"
            )
        
        if allowed_hours and current_hour not in allowed_hours:
            raise SafeguardException(
                f"Outside allowed schedule: {current_hour}h not in {allowed_hours}"
            )
        
        logger.debug(f"✅ Schedule OK: {current_day} {current_hour}:00")
        return True
    
    def check_max_instances_limit(self, current_count: int) -> bool:
        """
        Check if we haven't exceeded max instances per run.
        
        Args:
            current_count: Number of instances already processed
            
        Returns:
            True if under limit
            
        Raises:
            SafeguardException: If limit exceeded
        """
        max_instances = self.config.get('protection', {}).get(
            'max_instances_per_run', 3
        )
        
        if current_count >= max_instances:
            raise SafeguardException(
                f"Max instances limit reached: {current_count}/{max_instances}"
            )
        
        return True
    
    def validate_all(
        self,
        instance_id: str,
        instance_tags: Dict,
        launch_time: datetime,
        confidence: float,
        idle_days: int,
        current_count: int = 0
    ) -> Dict:
        """
        Run all safeguard checks.
        
        Args:
            instance_id: EC2 instance ID
            instance_tags: Instance tags
            launch_time: Instance launch time
            confidence: Detection confidence score
            idle_days: Days instance has been idle
            current_count: Number of instances already processed
            
        Returns:
            Dict with validation results
            
        Raises:
            SafeguardException: If any check fails
        """
        logger.info(f"🔍 Running safeguard checks for {instance_id}")
        
        results = {
            'instance_id': instance_id,
            'checks_passed': [],
            'checks_failed': [],
            'safe_to_proceed': False,
            'reason': None
        }
        
        try:
            # Check 1: Auto-remediation enabled
            if not self.is_auto_remediation_enabled():
                results['reason'] = "Auto-remediation disabled globally"
                results['checks_failed'].append('global_enabled')
                return results
            
            # Check 2: Whitelist
            if self.is_whitelisted(instance_id, instance_tags):
                results['reason'] = "Instance is whitelisted"
                results['checks_failed'].append('whitelist')
                return results
            results['checks_passed'].append('whitelist')
            
            # Check 3: Instance age
            self.check_instance_age(launch_time)
            results['checks_passed'].append('instance_age')
            
            # Check 4: Confidence score
            self.check_confidence_score(confidence)
            results['checks_passed'].append('confidence')
            
            # Check 5: Idle duration
            self.check_idle_duration(idle_days)
            results['checks_passed'].append('idle_duration')
            
            # Check 6: Schedule
            self.check_schedule()
            results['checks_passed'].append('schedule')
            
            # Check 7: Max instances limit
            self.check_max_instances_limit(current_count)
            results['checks_passed'].append('max_instances')
            
            # All checks passed
            results['safe_to_proceed'] = True
            logger.info(f"✅ All safeguard checks PASSED for {instance_id}")
            
        except SafeguardException as e:
            results['reason'] = str(e)
            logger.warning(f"❌ Safeguard check FAILED: {e}")
        
        return results


# Quick test
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    
    safeguards = Safeguards()
    
    # Test with fake instance
    test_result = safeguards.validate_all(
        instance_id='i-test123',
        instance_tags={'Environment': 'dev'},
        launch_time=datetime.now() - timedelta(days=60),
        confidence=0.85,
        idle_days=20,
        current_count=0
    )
    
    print("\n" + "="*50)
    print("SAFEGUARD TEST RESULTS")
    print("="*50)
    print(f"Safe to proceed: {test_result['safe_to_proceed']}")
    print(f"Checks passed: {test_result['checks_passed']}")
    print(f"Checks failed: {test_result['checks_failed']}")
    print(f"Reason: {test_result['reason']}")
    print("="*50)