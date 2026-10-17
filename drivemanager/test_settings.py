"""Test settings for Drive — SQLite by default (no PostgreSQL needed locally).

DRV-71: when POSTGRES_HOST is set (the CI test-gate's postgres sidecar), inherit
the base PostgreSQL DATABASES instead. Concurrency tests such as
test_two_writer_race_one_wins_one_409 skip under SQLite (its table-level write
serialization can't exercise the optimistic-concurrency race), so the deploy-
blocking `test` job must run against PostgreSQL for that contract to actually gate.
"""
import os
from drivemanager.settings import *  # noqa: F403

if not os.environ.get('POSTGRES_HOST'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': ':memory:',
        }
    }

# Disable file logging for tests
LOGGING['handlers']['file'] = {  # noqa: F405
    'class': 'logging.StreamHandler',
    'formatter': 'verbose',
}

# django.contrib.postgres is required for SearchVectorField even in SQLite tests
INSTALLED_APPS = list(INSTALLED_APPS) + ['django.contrib.postgres']  # noqa: F405

# Suppress known test-only system check warnings
SILENCED_SYSTEM_CHECKS = ['urls.W005', 'postgres.E005']
