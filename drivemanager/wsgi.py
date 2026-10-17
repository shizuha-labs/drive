"""
WSGI config for Shizuha Drive project.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'drivemanager.settings')

application = get_wsgi_application()
