Quota Management
================

Shizuha Drive implements per-user storage quotas to manage disk usage.

Overview
--------

* **Default quota**: 5 GB per user
* **Quota tracking**: Real-time usage updates
* **Enforcement**: Upload blocked when quota exceeded
* **Trash**: Counts toward quota until emptied

Viewing Quotas
--------------

**List All Quotas:**

.. code-block:: bash

   ./scripts/drive-list-quotas.sh

Output:

.. code-block:: text

   === Shizuha Drive - User Quotas ===

   User ID      Used         Quota        Usage %    Files
   ------------------------------------------------------------
   3            5.2 GB       100.0 GB        5.2%    150
   5            1.0 GB       5.0 GB         20.0%    45
   8            0.0 B        5.0 GB          0.0%    0

**Via API:**

.. code-block:: bash

   curl -X GET "https://your-domain/shizuha-drive/api/quota/" \
     -H "Authorization: Bearer YOUR_TOKEN"

Setting Quotas
--------------

**Using CLI Script:**

.. code-block:: bash

   # Set quota by email
   ./scripts/drive-set-quota.sh user@example.com 100

   # Script will:
   # 1. Look up user ID from Shizuha ID by email
   # 2. Create or update quota in Drive database

**Script Output:**

.. code-block:: text

   === Shizuha Drive Quota Manager ===

   Email:    user@example.com
   Quota:    100 GB (107374182400 bytes)

   Finding Shizuha Drive pod...
   Found pod: shizuha-shizuha-drive-58b6b6c95-gfhqh

   Looking up user by email in Shizuha ID...
   User ID: 3

   Setting storage quota...
   Updated quota: 5 GB -> 100 GB

   === Quota Set Successfully ===

   User:         user@example.com
   User ID:      3
   New Quota:    100 GB
   Currently Used: 5.2 GB

**Using Django Shell:**

.. code-block:: bash

   kubectl -n shizuha exec -it deploy/shizuha-shizuha-drive -- \
     python manage.py shell

.. code-block:: python

   from drive.models import StorageQuota

   # Set quota for user ID 3 to 100GB
   quota, created = StorageQuota.objects.update_or_create(
       owner_id='3',
       defaults={'quota_bytes': 100 * 1024**3}  # 100GB
   )
   print(f"Quota: {quota.quota_bytes / 1024**3} GB")

**Via Admin API:**

.. code-block:: bash

   # Requires admin privileges
   curl -X PATCH "https://your-domain/shizuha-drive/api/admin/quota/3/" \
     -H "Authorization: Bearer ADMIN_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"quota_bytes": 107374182400}'

Quota Tiers
-----------

Configure quota tiers based on user type:

.. list-table::
   :header-rows: 1
   :widths: 25 25 50

   * - Tier
     - Quota
     - Description
   * - Free
     - 5 GB
     - Default for all users
   * - Pro
     - 100 GB
     - Premium subscribers
   * - Team
     - 1 TB
     - Team/organization accounts
   * - Enterprise
     - Unlimited
     - Custom enterprise agreements

**Automation with User Roles:**

Integrate with Shizuha ID to automatically set quotas:

.. code-block:: python

   # In your user sync script
   def sync_user_quota(user_id, subscription_tier):
       quota_map = {
           'free': 5 * 1024**3,
           'pro': 100 * 1024**3,
           'team': 1024 * 1024**3,
           'enterprise': 100 * 1024**4,  # 100TB
       }

       StorageQuota.objects.update_or_create(
           owner_id=str(user_id),
           defaults={'quota_bytes': quota_map.get(subscription_tier, quota_map['free'])}
       )

Quota Enforcement
-----------------

**How It Works:**

1. Before upload, API checks ``StorageQuota.can_upload(file_size)``
2. If quota would be exceeded, returns HTTP 507 (Insufficient Storage)
3. After successful upload, ``used_bytes`` is incremented

**Upload Blocked Response:**

.. code-block:: json

   {
     "detail": "Storage quota exceeded. You have used 5.0 GB of 5.0 GB.",
     "code": "quota_exceeded",
     "quota_bytes": 5368709120,
     "used_bytes": 5368709120,
     "file_size": 1048576
   }

Quota Recalculation
-------------------

Quotas are automatically recalculated daily at 4:00 AM to fix any drift:

.. code-block:: bash

   # Manual recalculation
   kubectl -n shizuha exec deploy/shizuha-shizuha-drive -- \
     python manage.py shell -c "
   from drive.tasks import recalculate_quotas
   recalculate_quotas()
   "

**Force Immediate Recalculation:**

.. code-block:: python

   from django.db.models import Sum, Count
   from drive.models import File, StorageQuota

   # For a specific user
   owner_id = '3'
   usage = File.objects.filter(owner_id=owner_id).aggregate(
       total_size=Sum('size'),
       file_count=Count('id')
   )

   StorageQuota.objects.update_or_create(
       owner_id=owner_id,
       defaults={
           'used_bytes': usage['total_size'] or 0,
           'file_count': usage['file_count'] or 0
       }
   )

Trash and Quota
---------------

**Important:** Files in trash still count toward quota!

To free up space, users must:

1. Empty trash manually, or
2. Wait for automatic cleanup (30 days)

**Admin Force Empty Trash:**

.. code-block:: python

   from drive.models import File
   from django.utils import timezone
   from django.core.files.storage import default_storage

   # Force delete all trash for user
   owner_id = '3'
   trashed_files = File.objects.filter(
       owner_id=owner_id,
       is_trashed=True
   )

   for f in trashed_files:
       if f.file:
           default_storage.delete(f.file.name)
       f.delete()

Monitoring Quotas
-----------------

**Prometheus Metrics:**

Expose quota metrics for monitoring:

.. code-block:: python

   # Custom management command
   from prometheus_client import Gauge

   quota_usage = Gauge(
       'shizuha_drive_quota_usage_bytes',
       'Storage quota usage in bytes',
       ['owner_id']
   )

   for quota in StorageQuota.objects.all():
       quota_usage.labels(owner_id=quota.owner_id).set(quota.used_bytes)

**Alert Rules:**

.. code-block:: yaml

   # Prometheus alert
   - alert: DriveQuotaNearLimit
     expr: shizuha_drive_quota_usage_bytes / shizuha_drive_quota_bytes > 0.9
     for: 1h
     labels:
       severity: warning
     annotations:
       summary: "User {{ $labels.owner_id }} is near storage quota"

Bulk Quota Updates
------------------

**Set quota for multiple users:**

.. code-block:: python

   from drive.models import StorageQuota

   # Upgrade all users to 50GB
   StorageQuota.objects.filter(quota_bytes=5368709120).update(
       quota_bytes=50 * 1024**3
   )

   # Set quota for specific users
   user_ids = ['3', '5', '8']
   for uid in user_ids:
       StorageQuota.objects.update_or_create(
           owner_id=uid,
           defaults={'quota_bytes': 100 * 1024**3}
       )

**Import from CSV:**

.. code-block:: bash

   # quotas.csv: email,quota_gb
   # user1@example.com,100
   # user2@example.com,50

   while IFS=, read -r email quota_gb; do
     ./scripts/drive-set-quota.sh "$email" "$quota_gb"
   done < quotas.csv
