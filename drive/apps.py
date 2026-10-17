from django.apps import AppConfig


class DriveConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'drive'
    verbose_name = 'Shizuha Drive'

    def ready(self):
        # Import signals
        import drive.signals  # noqa
