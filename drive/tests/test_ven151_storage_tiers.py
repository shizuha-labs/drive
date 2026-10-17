"""
Tests for VEN-151: Storage tier enforcement for Shizuha Drive

Tests cover:
- StorageTier model (creation, properties, soft limit)
- UserTier model (creation, pending changes, limit checks)
- Tier API endpoints (list tiers, get my tier, schedule change)
- Quota view with tier warnings
"""
import uuid
import threading
import time
from unittest.mock import patch
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from datetime import datetime, timedelta, timezone as dt_timezone
from rest_framework.test import APITestCase, APIClient
from rest_framework import status

from drive.models import (
    File,
    ScheduledMechanismRun,
    ServiceStorageUsage,
    StorageQuota,
    StorageQuotaReservation,
    StorageTier,
    UserTier,
)


class StorageTierModelTests(TestCase):
    """Test StorageTier model"""

    def setUp(self):
        self.free_tier, _ = StorageTier.objects.get_or_create(
            slug='free',
            defaults=dict(
                name='Free',
                quota_bytes=5 * 1024 * 1024 * 1024,  # 5 GB
                price_monthly=0,
                price_yearly=0,
                description='Free tier',
                sort_order=0,
            ),
        )
        self.pro_tier, _ = StorageTier.objects.get_or_create(
            slug='pro',
            defaults=dict(
                name='Pro',
                quota_bytes=50 * 1024 * 1024 * 1024,  # 50 GB
                price_monthly=299,
                price_yearly=0,
                description='Pro tier',
                sort_order=1,
            ),
        )

    def test_tier_creation(self):
        """Test basic tier creation"""
        assert self.free_tier.name == 'Free'
        assert self.free_tier.slug == 'free'
        assert self.free_tier.quota_bytes == 5 * 1024 * 1024 * 1024
        assert self.free_tier.is_active is True
        assert self.pro_tier.price_monthly == 299
        assert self.pro_tier.price_yearly == 0

    def test_tier_quota_human(self):
        """Test human-readable quota"""
        assert self.free_tier.quota_human == '5 GB'
        assert self.pro_tier.quota_human == '50 GB'

    def test_soft_limit_bytes(self):
        """Test 90% soft limit calculation"""
        expected_soft = int(5 * 1024 * 1024 * 1024 * 0.9)
        assert self.free_tier.soft_limit_bytes == expected_soft

    def test_tier_ordering(self):
        """Test tiers are ordered by sort_order"""
        tiers = StorageTier.objects.all()
        assert tiers[0].slug == 'free'
        assert tiers[1].slug == 'pro'

    def test_tier_str(self):
        """Test string representation"""
        assert str(self.free_tier) == 'Free (5 GB)'
        assert str(self.pro_tier) == 'Pro (50 GB)'


class UserTierModelTests(TestCase):
    """Test UserTier model"""

    def setUp(self):
        self.free_tier, _ = StorageTier.objects.get_or_create(
            slug='free',
            defaults=dict(
                name='Free',
                quota_bytes=5 * 1024 * 1024 * 1024,
                sort_order=0,
            ),
        )
        self.pro_tier, _ = StorageTier.objects.get_or_create(
            slug='pro',
            defaults=dict(
                name='Pro',
                quota_bytes=50 * 1024 * 1024 * 1024,
                price_monthly=299,
                sort_order=1,
            ),
        )
        self.user_id = 'user-123'
        self.user_tier = UserTier.objects.create(
            owner_id=self.user_id,
            tier=self.free_tier,
            quota_bytes=self.free_tier.quota_bytes,
        )

    def test_user_tier_creation(self):
        """Test basic user tier creation"""
        assert self.user_tier.owner_id == self.user_id
        assert self.user_tier.tier == self.free_tier
        assert self.user_tier.quota_bytes == self.free_tier.quota_bytes

    def test_soft_limit_bytes(self):
        """Test 90% soft limit on user tier"""
        expected = int(self.free_tier.quota_bytes * 0.9)
        assert self.user_tier.soft_limit_bytes == expected

    def test_schedule_change(self):
        """Test scheduling a tier change"""
        reference = timezone.now()
        self.user_tier.schedule_change(
            self.pro_tier,
            self.pro_tier.quota_bytes,
            now=reference,
        )
        assert self.user_tier.pending_tier == self.pro_tier
        assert self.user_tier.pending_quota_bytes == self.pro_tier.quota_bytes
        assert self.user_tier.next_billing_date == UserTier.next_cycle_date(
            reference,
            'monthly',
        )

    def test_apply_pending_changes(self):
        """A due paid change applies only after trusted entitlement."""
        reference = timezone.now()
        self.user_tier.schedule_change(
            self.pro_tier,
            self.pro_tier.quota_bytes,
            now=reference,
        )
        due = self.user_tier.next_billing_date
        assert self.user_tier.apply_pending_changes(now=due) is False
        self.user_tier.authorize_pending_change('entitlement:test:paid')
        assert self.user_tier.apply_pending_changes(now=due) is True
        assert self.user_tier.tier == self.pro_tier
        assert self.user_tier.quota_bytes == self.pro_tier.quota_bytes
        assert self.user_tier.pending_tier is None
        assert self.user_tier.pending_quota_bytes is None

    def test_apply_pending_changes_noop(self):
        """Test apply_pending_changes is noop when no pending change"""
        self.user_tier.apply_pending_changes()
        assert self.user_tier.tier == self.free_tier  # Unchanged

    def test_is_over_soft_limit(self):
        """Test soft limit detection"""
        StorageQuota.objects.create(
            owner_id=self.user_id,
            used_bytes=int(self.free_tier.quota_bytes * 0.95),  # 95% used
        )
        assert self.user_tier.is_over_soft_limit is True

    def test_is_not_over_soft_limit(self):
        """Test not over soft limit"""
        StorageQuota.objects.create(
            owner_id=self.user_id,
            used_bytes=int(self.free_tier.quota_bytes * 0.5),  # 50% used
        )
        assert self.user_tier.is_over_soft_limit is False

    def test_is_over_hard_limit(self):
        """Test hard limit detection"""
        StorageQuota.objects.create(
            owner_id=self.user_id,
            used_bytes=self.free_tier.quota_bytes + 1,  # Over quota
        )
        assert self.user_tier.is_over_hard_limit is True

    def test_user_tier_str(self):
        """Test string representation"""
        assert str(self.user_tier) == f'{self.user_id} → Free'


class TierAPITests(APITestCase):
    """Test tier API endpoints"""

    def setUp(self):
        self.client = APIClient()
        # Create tiers
        self.free_tier, _ = StorageTier.objects.get_or_create(
            slug='free',
            defaults=dict(
                name='Free',
                quota_bytes=5 * 1024 * 1024 * 1024,
                price_monthly=0,
                price_yearly=0,
                description='Free tier',
                sort_order=0,
            ),
        )
        self.pro_tier, _ = StorageTier.objects.get_or_create(
            slug='pro',
            defaults=dict(
                name='Pro',
                quota_bytes=50 * 1024 * 1024 * 1024,
                price_monthly=299,
                price_yearly=0,
                description='Pro tier',
                sort_order=1,
            ),
        )
        self.team_tier, _ = StorageTier.objects.get_or_create(
            slug='team',
            defaults=dict(
                name='Team',
                quota_bytes=200 * 1024 * 1024 * 1024,
                price_monthly=799,
                price_yearly=0,
                description='Team tier',
                sort_order=2,
            ),
        )

        # Create a mock user
        self.user_id = 'test-user-001'
        self._create_mock_auth()

    def _create_mock_auth(self):
        """Set up mock JWT authentication"""
        from drive.authentication import FederatedUser
        user = FederatedUser(
            user_id=self.user_id,
            email='test@shizuha.com',
            username='testuser',
        )
        self.client.force_authenticate(user=user)

    def test_list_tiers(self):
        """Test GET /api/tiers/"""
        response = self.client.get('/api/tiers/')
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        results = data.get('results', data)  # Handle paginated or flat response
        assert len(results) == 3
        slugs = [t['slug'] for t in results]
        assert 'free' in slugs
        assert 'pro' in slugs
        assert 'team' in slugs

    def test_list_tiers_ordering(self):
        """Test tiers are returned in sort_order"""
        response = self.client.get('/api/tiers/')
        data = response.json()
        results = data.get('results', data)
        assert results[0]['slug'] == 'free'
        assert results[1]['slug'] == 'pro'
        assert results[2]['slug'] == 'team'

    def test_get_tier_by_slug(self):
        """Test GET /api/tiers/free/"""
        response = self.client.get('/api/tiers/free/')
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['slug'] == 'free'
        assert data['quota_bytes'] == 5 * 1024 * 1024 * 1024
        assert 'quota_human' in data
        assert 'soft_limit_bytes' in data

    def test_get_tier_not_found(self):
        """Test GET /api/tiers/nonexistent/"""
        response = self.client.get('/api/tiers/nonexistent/')
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_get_my_tier_no_assignment(self):
        """Test GET /api/tiers/my/ when user has no tier"""
        response = self.client.get('/api/tiers/my/')
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        # Should return default free tier info
        assert data['tier']['slug'] == 'free'
        assert data['quota_bytes'] == self.free_tier.quota_bytes
        assert data['pending_change'] is None

    def test_get_my_tier_with_assignment(self):
        """Test GET /api/tiers/my/ when user has a tier"""
        UserTier.objects.create(
            owner_id=self.user_id,
            tier=self.pro_tier,
            quota_bytes=self.pro_tier.quota_bytes,
        )
        response = self.client.get('/api/tiers/my/')
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['tier_name'] == 'Pro'
        assert data['tier_slug'] == 'pro'
        assert data['quota_bytes'] == self.pro_tier.quota_bytes

    def test_paid_tier_first_time_records_non_entitling_intent(self):
        """An unpaid caller cannot activate Pro effective quota."""
        response = self.client.post('/api/tiers/my/', {
            'tier_id': str(self.pro_tier.id),
        })
        assert response.status_code == status.HTTP_202_ACCEPTED
        data = response.json()
        assert data['status'] == 'upgrade_intent_recorded'

        user_tier = UserTier.objects.get(owner_id=self.user_id)
        assert user_tier.tier == self.free_tier
        assert user_tier.quota_bytes == self.free_tier.quota_bytes
        assert user_tier.pending_tier == self.pro_tier
        assert user_tier.pending_entitlement_reference is None

    def test_schedule_tier_change(self):
        """Paid POST records a due intent without effective entitlement."""
        # First activate free tier
        UserTier.objects.create(
            owner_id=self.user_id,
            tier=self.free_tier,
            quota_bytes=self.free_tier.quota_bytes,
        )

        # Schedule upgrade to pro
        response = self.client.post('/api/tiers/my/', {
            'tier_id': str(self.pro_tier.id),
        })
        assert response.status_code == status.HTTP_202_ACCEPTED
        data = response.json()
        assert data['status'] == 'upgrade_intent_recorded'

        # Verify pending change
        user_tier = UserTier.objects.get(owner_id=self.user_id)
        assert user_tier.pending_tier == self.pro_tier
        assert user_tier.pending_quota_bytes == self.pro_tier.quota_bytes
        assert user_tier.next_billing_date is not None
        assert user_tier.tier == self.free_tier

    def test_schedule_tier_change_invalid_tier(self):
        """Test POST with invalid tier ID"""
        response = self.client.post('/api/tiers/my/', {
            'tier_id': str(uuid.uuid4()),
        })
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_post_to_due_task_applies_free_downgrade_next_cycle(self):
        """POST intent → deterministic due date → beat task → effective quota."""
        from drive.tasks import apply_pending_tier_changes

        UserTier.objects.create(
            owner_id=self.user_id,
            tier=self.pro_tier,
            quota_bytes=self.pro_tier.quota_bytes,
            billing_cycle='monthly',
        )
        fixed_now = datetime(2026, 1, 31, 12, 0, tzinfo=dt_timezone.utc)
        with patch('drive.models.timezone.now', return_value=fixed_now):
            response = self.client.post('/api/tiers/my/', {
                'tier_id': str(self.free_tier.id),
            })

        assert response.status_code == status.HTTP_200_OK
        assert response.json()['status'] == 'scheduled'
        user_tier = UserTier.objects.get(owner_id=self.user_id)
        assert user_tier.next_billing_date == datetime(
            2026, 2, 28, 12, 0, tzinfo=dt_timezone.utc,
        )

        with patch('drive.tasks.timezone.now', return_value=user_tier.next_billing_date):
            result = apply_pending_tier_changes()

        assert result['users_updated'] == 1
        user_tier.refresh_from_db()
        assert user_tier.tier == self.free_tier
        assert user_tier.quota_bytes == self.free_tier.quota_bytes

    def test_public_post_cannot_change_or_postpone_paid_billing_boundary(self):
        """Caller-supplied cycle and repeats cannot postpone a paid downgrade."""
        trusted_boundary = datetime(2026, 2, 28, 12, 0, tzinfo=dt_timezone.utc)
        UserTier.objects.create(
            owner_id=self.user_id,
            tier=self.pro_tier,
            quota_bytes=self.pro_tier.quota_bytes,
            billing_cycle='monthly',
            next_billing_date=trusted_boundary,
        )

        with patch(
            'drive.models.timezone.now',
            return_value=datetime(2026, 1, 31, 12, 0, tzinfo=dt_timezone.utc),
        ):
            first = self.client.post('/api/tiers/my/', {
                'tier_id': str(self.free_tier.id),
                'billing_cycle': 'yearly',
            })
        assert first.status_code == status.HTTP_200_OK
        user_tier = UserTier.objects.get(owner_id=self.user_id)
        assert user_tier.billing_cycle == 'monthly'
        assert user_tier.next_billing_date == trusted_boundary

        with patch(
            'drive.models.timezone.now',
            return_value=datetime(2026, 3, 1, 12, 0, tzinfo=dt_timezone.utc),
        ):
            repeated = self.client.post('/api/tiers/my/', {
                'tier_id': str(self.free_tier.id),
                'billing_cycle': 'yearly',
            })
        assert repeated.status_code == status.HTTP_200_OK
        user_tier.refresh_from_db()
        assert user_tier.billing_cycle == 'monthly'
        assert user_tier.next_billing_date == trusted_boundary

    def test_quota_view_with_tier_warnings(self):
        """Test quota view includes tier info and warnings"""
        UserTier.objects.create(
            owner_id=self.user_id,
            tier=self.free_tier,
            quota_bytes=self.free_tier.quota_bytes,
        )
        # Create files to push usage over soft limit
        File.objects.create(
            owner_id=self.user_id,
            name='big_file.bin',
            size=int(self.free_tier.quota_bytes * 0.95),
            file='test_files/big_file.bin',
            mime_type='application/octet-stream',
        )

        response = self.client.get('/api/quota/')
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert 'tier' in data
        assert data['tier']['tier_slug'] == 'free'

    def test_quota_view_reports_unified_service_usage_used_by_admission(self):
        """Visible usage must match Drive + service usage enforcement."""
        UserTier.objects.create(
            owner_id=self.user_id,
            tier=self.free_tier,
            quota_bytes=self.free_tier.quota_bytes,
        )
        StorageQuota.objects.create(
            owner_id=self.user_id,
            quota_bytes=self.free_tier.quota_bytes,
        )
        drive_bytes = self.free_tier.quota_bytes // 5
        service_bytes = self.free_tier.quota_bytes * 3 // 4
        File.objects.create(
            owner_id=self.user_id,
            name='drive-usage.bin',
            size=drive_bytes,
            file='test_files/drive-usage.bin',
            mime_type='application/octet-stream',
        )
        ServiceStorageUsage.objects.create(
            owner_id=self.user_id,
            service='wiki',
            used_bytes=service_bytes,
        )

        response = self.client.get('/api/quota/')
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['total_used_bytes'] == drive_bytes + service_bytes
        assert data['total_usage_percent'] == 95.0
        assert data['total_used_human'] != data['used_human']
        assert len(data['warnings']) > 0
        assert data['warnings'][0]['level'] == 'soft_limit'

    def test_quota_view_hard_limit_warning(self):
        """Test quota view shows hard limit warning"""
        UserTier.objects.create(
            owner_id=self.user_id,
            tier=self.free_tier,
            quota_bytes=self.free_tier.quota_bytes,
        )
        # Create files to push usage over hard limit
        File.objects.create(
            owner_id=self.user_id,
            name='big_file.bin',
            size=self.free_tier.quota_bytes + 1000,
            file='test_files/big_file.bin',
            mime_type='application/octet-stream',
        )

        response = self.client.get('/api/quota/')
        data = response.json()
        assert len(data['warnings']) > 0
        assert data['warnings'][0]['level'] == 'hard_limit'

    def test_quota_view_no_warnings(self):
        """Test quota view has no warnings when usage is low"""
        UserTier.objects.create(
            owner_id=self.user_id,
            tier=self.pro_tier,
            quota_bytes=self.pro_tier.quota_bytes,
        )
        File.objects.create(
            owner_id=self.user_id,
            name='small_file.txt',
            size=1000,
            file='test_files/small_file.txt',
            mime_type='text/plain',
        )

        response = self.client.get('/api/quota/')
        data = response.json()
        assert len(data['warnings']) == 0

    def test_quota_view_no_tier_fallback(self):
        """Test quota view works without a UserTier (falls back to free tier)"""
        File.objects.create(
            owner_id=self.user_id,
            name='small_file.txt',
            size=1000,
            file='test_files/small_file.txt',
            mime_type='text/plain',
        )
        response = self.client.get('/api/quota/')
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert 'tier' in data
        assert 'warnings' in data


class TierEnforcementTests(TestCase):
    """Test tier enforcement on uploads"""

    def setUp(self):
        self.free_tier, _ = StorageTier.objects.get_or_create(
            slug='free',
            defaults=dict(
                name='Free',
                quota_bytes=5 * 1024 * 1024 * 1024,
                sort_order=0,
            ),
        )
        self.user_id = 'user-enforce-001'

    def test_check_upload_quota_over_hard_limit(self):
        """Test upload check returns False when over hard limit"""
        user_tier = UserTier.objects.create(
            owner_id=self.user_id,
            tier=self.free_tier,
            quota_bytes=self.free_tier.quota_bytes,
        )
        StorageQuota.objects.create(
            owner_id=self.user_id,
            used_bytes=self.free_tier.quota_bytes + 1,
        )
        assert user_tier.is_over_hard_limit is True

    def test_check_upload_quota_under_limit(self):
        """Test upload check returns True when under limit"""
        user_tier = UserTier.objects.create(
            owner_id=self.user_id,
            tier=self.free_tier,
            quota_bytes=self.free_tier.quota_bytes,
        )
        StorageQuota.objects.create(
            owner_id=self.user_id,
            used_bytes=1000,
        )
        assert user_tier.is_over_hard_limit is False


class TierTaskTests(TestCase):
    """Test tier-related Celery tasks"""

    def setUp(self):
        self.free_tier, _ = StorageTier.objects.get_or_create(
            slug='free',
            defaults=dict(
                name='Free',
                quota_bytes=5 * 1024 * 1024 * 1024,
                sort_order=0,
            ),
        )
        self.pro_tier, _ = StorageTier.objects.get_or_create(
            slug='pro',
            defaults=dict(
                name='Pro',
                quota_bytes=50 * 1024 * 1024 * 1024,
                price_monthly=299,
                sort_order=1,
            ),
        )

    def test_apply_pending_tier_changes(self):
        """Test apply_pending_tier_changes task"""
        from drive.tasks import apply_pending_tier_changes

        # Create user with pending change past billing date
        user_tier = UserTier.objects.create(
            owner_id='user-task-001',
            tier=self.free_tier,
            quota_bytes=self.free_tier.quota_bytes,
            pending_tier=self.pro_tier,
            pending_quota_bytes=self.pro_tier.quota_bytes,
            pending_entitlement_reference='entitlement:test:task',
            next_billing_date=timezone.now() - timedelta(days=1),  # Past due
        )

        result = apply_pending_tier_changes()
        assert result['users_updated'] == 1

        # Verify change was applied
        user_tier.refresh_from_db()
        assert user_tier.tier == self.pro_tier
        assert user_tier.pending_tier is None
        run = ScheduledMechanismRun.objects.get(mechanism='apply_pending_tier_changes')
        assert run.last_succeeded_at is not None
        assert run.consecutive_failures == 0

    def test_apply_pending_tier_changes_future(self):
        """Test pending changes not applied before billing date"""
        from drive.tasks import apply_pending_tier_changes

        user_tier = UserTier.objects.create(
            owner_id='user-task-002',
            tier=self.free_tier,
            quota_bytes=self.free_tier.quota_bytes,
            pending_tier=self.pro_tier,
            pending_quota_bytes=self.pro_tier.quota_bytes,
            next_billing_date=timezone.now() + timedelta(days=30),  # Future
        )

        result = apply_pending_tier_changes()
        assert result['users_updated'] == 0

        # Verify no change
        user_tier.refresh_from_db()
        assert user_tier.tier == self.free_tier
        assert user_tier.pending_tier == self.pro_tier

    def test_unpaid_due_upgrade_does_not_apply(self):
        from drive.tasks import apply_pending_tier_changes

        user_tier = UserTier.objects.create(
            owner_id='user-task-unpaid',
            tier=self.free_tier,
            quota_bytes=self.free_tier.quota_bytes,
            pending_tier=self.pro_tier,
            pending_quota_bytes=self.pro_tier.quota_bytes,
            next_billing_date=timezone.now() - timedelta(days=1),
        )
        assert apply_pending_tier_changes()['users_updated'] == 0
        user_tier.refresh_from_db()
        assert user_tier.tier == self.free_tier
        assert user_tier.pending_tier == self.pro_tier

    def test_pending_tier_task_is_wired_to_beat(self):
        from django.conf import settings
        from celery.schedules import crontab

        assert settings.CELERY_BEAT_SCHEDULE['apply-pending-storage-tier-changes'] == {
            'task': 'drive.tasks.apply_pending_tier_changes',
            'schedule': crontab(minute='*/15'),
        }

    @patch('drive.tasks._send_pending_tier_failure_alert')
    def test_task_failure_persists_liveness_and_alerts_named_owner(self, alert):
        from drive.tasks import apply_pending_tier_changes

        UserTier.objects.create(
            owner_id='user-task-failure',
            tier=self.free_tier,
            quota_bytes=self.free_tier.quota_bytes,
            pending_tier=self.pro_tier,
            pending_quota_bytes=self.pro_tier.quota_bytes,
            pending_entitlement_reference='entitlement:test:failure',
            next_billing_date=timezone.now() - timedelta(days=1),
        )
        with patch.object(UserTier, 'apply_pending_changes', side_effect=RuntimeError('boom')):
            with self.assertRaisesRegex(RuntimeError, 'boom'):
                apply_pending_tier_changes()

        run = ScheduledMechanismRun.objects.get(mechanism='apply_pending_tier_changes')
        assert run.last_failed_at is not None
        assert run.last_error == 'boom'
        assert run.consecutive_failures == 1
        alert.assert_called_once()

    def test_scheduler_status_fails_loud_without_killing_serving_pods(self):
        ScheduledMechanismRun.objects.create(
            mechanism='apply_pending_tier_changes',
            last_succeeded_at=timezone.now() - timedelta(hours=40),
        )
        status_response = self.client.get('/api/pending-tier-statusz/')
        assert status_response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert status_response.json()['pending_tier_scheduler']['state'] == 'stale'

        health_response = self.client.get('/api/health/')
        assert health_response.status_code == status.HTTP_200_OK
        assert health_response.json() == {'status': 'healthy'}

    def test_kubernetes_health_does_not_arm_scheduler_staleness(self):
        assert not ScheduledMechanismRun.objects.exists()

        response = self.client.get('/api/health/')

        assert response.status_code == status.HTTP_200_OK
        assert not ScheduledMechanismRun.objects.exists()


class TierQuotaUploadAdmissionTests(APITestCase):
    """Test that tier quota is enforced in upload admission paths"""

    def setUp(self):
        self.client = APIClient()
        self.user_id = str(uuid.uuid4())
        from drive.authentication import FederatedUser
        self.user = FederatedUser(
            user_id=self.user_id,
            email='tier-test-user@shizuha.com',
            username='tier-test-user',
        )
        self.client.force_authenticate(user=self.user)

        self.free_tier, _ = StorageTier.objects.get_or_create(
            slug='free',
            defaults=dict(
                name='Free',
                quota_bytes=5 * 1024 * 1024 * 1024,  # 5 GB
                price_monthly=0,
                price_yearly=0,
                description='Free tier',
                sort_order=0,
            ),
        )
        self.pro_tier, _ = StorageTier.objects.get_or_create(
            slug='pro',
            defaults=dict(
                name='Pro',
                quota_bytes=50 * 1024 * 1024 * 1024,  # 50 GB
                price_monthly=299,
                price_yearly=0,
                description='Pro tier',
                sort_order=1,
            ),
        )

    def test_free_tier_blocks_upload_above_5gb(self):
        """Free tier user with 5GB quota should be blocked from uploading >5GB"""
        # Create a free-tier UserTier
        UserTier.objects.create(
            owner_id=self.user_id,
            tier=self.free_tier,
            quota_bytes=self.free_tier.quota_bytes,
        )
        quota, _ = StorageQuota.objects.get_or_create(owner_id=self.user_id)
        # Simulate 5GB used
        quota.used_bytes = 5 * 1024 * 1024 * 1024
        quota.save(update_fields=['used_bytes'])

        # can_upload should return False for any additional byte
        self.assertFalse(quota.can_upload(1))

    def test_pro_tier_allows_upload_above_5gb(self):
        """Pro tier user with 50GB quota should be allowed to upload >5GB"""
        # Create a pro-tier UserTier
        UserTier.objects.create(
            owner_id=self.user_id,
            tier=self.pro_tier,
            quota_bytes=self.pro_tier.quota_bytes,
        )
        quota, _ = StorageQuota.objects.get_or_create(owner_id=self.user_id)
        # Simulate 5GB used (would be at limit for free, but pro has 50GB)
        quota.used_bytes = 5 * 1024 * 1024 * 1024
        quota.save(update_fields=['used_bytes'])

        # can_upload should return True — tier quota is 50GB
        self.assertTrue(quota.can_upload(1))
        # After sync, quota_bytes should reflect the tier
        quota.refresh_from_db()
        self.assertEqual(quota.quota_bytes, self.pro_tier.quota_bytes)

    def test_pro_tier_blocks_upload_above_50gb(self):
        """Pro tier user should be blocked at 50GB hard limit"""
        UserTier.objects.create(
            owner_id=self.user_id,
            tier=self.pro_tier,
            quota_bytes=self.pro_tier.quota_bytes,
        )
        quota, _ = StorageQuota.objects.get_or_create(owner_id=self.user_id)
        quota.used_bytes = 50 * 1024 * 1024 * 1024
        quota.save(update_fields=['used_bytes'])

        self.assertFalse(quota.can_upload(1))

    def test_no_tier_falls_back_to_default_quota(self):
        """User without a UserTier should use the default 5GB StorageQuota"""
        quota, _ = StorageQuota.objects.get_or_create(owner_id=self.user_id)
        self.assertEqual(quota.quota_bytes, 5 * 1024 * 1024 * 1024)
        quota.used_bytes = 5 * 1024 * 1024 * 1024
        quota.save(update_fields=['used_bytes'])
        self.assertFalse(quota.can_upload(1))

    def test_internal_admission_reserves_then_commits_reported_usage(self):
        quota, _ = StorageQuota.objects.get_or_create(owner_id=self.user_id)
        quota.quota_bytes = 10
        quota.save(update_fields=['quota_bytes'])

        first = self.client.post(
            '/api/internal/storage/can-upload/',
            {'file_size': 6},
            format='json',
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertTrue(first.json()['allowed'])
        reservation_id = first.json()['reservation_id']

        second = self.client.post(
            '/api/internal/storage/can-upload/',
            {'file_size': 6},
            format='json',
        )
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertFalse(second.json()['allowed'])

        report = self.client.post(
            '/api/internal/storage/report/',
            {
                'service': 'wiki',
                'used_bytes': 6,
                'file_count': 1,
                'reservation_id': reservation_id,
            },
            format='json',
        )
        self.assertEqual(report.status_code, status.HTTP_200_OK)
        quota.refresh_from_db()
        self.assertEqual(quota.total_used_bytes, 6)
        self.assertEqual(quota.active_reserved_bytes, 0)


class AtomicQuotaReservationTests(TransactionTestCase):
    """Production-order concurrent admissions serialize on the quota row."""

    reset_sequences = True

    def test_two_admissions_cannot_exceed_hard_limit(self):
        if connection.vendor == 'sqlite':
            self.skipTest('SQLite cannot exercise row-level select_for_update semantics')

        owner_id = 'concurrent-tier-user'
        StorageQuota.objects.create(owner_id=owner_id, quota_bytes=10)
        barrier = threading.Barrier(2)
        results = []

        def reserve():
            from django.db import connection as thread_connection
            try:
                barrier.wait()
                with transaction.atomic():
                    reservation, _ = StorageQuota.reserve_upload(owner_id, 6)
                    results.append(reservation is not None)
                    if reservation:
                        time.sleep(0.1)
            finally:
                thread_connection.close()

        from django.db import transaction
        t1 = threading.Thread(target=reserve)
        t2 = threading.Thread(target=reserve)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(sorted(results), [False, True])
        quota = StorageQuota.objects.get(owner_id=owner_id)
        self.assertEqual(quota.active_reserved_bytes, 6)
        self.assertEqual(quota.total_remaining_bytes, 4)

    def test_two_pre_admitted_resumable_commits_preserve_usage_and_close_holds(self):
        """Production order: reserve twice, commit concurrently, then re-admit."""
        if connection.vendor == 'sqlite':
            self.skipTest('SQLite cannot exercise concurrent PostgreSQL row updates')

        owner_id = 'concurrent-resumable-commit-user'
        StorageQuota.objects.create(owner_id=owner_id, quota_bytes=10)
        first, _ = StorageQuota.reserve_upload(owner_id, 5)
        second, _ = StorageQuota.reserve_upload(owner_id, 5)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)

        barrier = threading.Barrier(2)
        errors = []

        def commit(reservation_id, suffix):
            from django.db import connection as thread_connection, transaction
            try:
                barrier.wait()
                with transaction.atomic():
                    File.objects.create(
                        owner_id=owner_id,
                        name=f'resumable-{suffix}.bin',
                        size=5,
                        file=f'test_files/resumable-{suffix}.bin',
                    )
                    reservation = StorageQuotaReservation.objects.get(pk=reservation_id)
                    reservation.mark_committed()
            except Exception as exc:  # surfaced in the main test thread
                errors.append(exc)
            finally:
                thread_connection.close()

        t1 = threading.Thread(target=commit, args=(first.pk, 'a'))
        t2 = threading.Thread(target=commit, args=(second.pk, 'b'))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(errors, [])
        quota = StorageQuota.objects.get(owner_id=owner_id)
        self.assertEqual(quota.used_bytes, 10)
        self.assertEqual(quota.file_count, 2)
        self.assertEqual(quota.active_reserved_bytes, 0)
        rejected, _ = StorageQuota.reserve_upload(owner_id, 1)
        self.assertIsNone(rejected)
