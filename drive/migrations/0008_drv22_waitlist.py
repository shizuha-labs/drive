# DRV-22 (VEN-49): free-validation waitlist / early-access capture model.

from django.db import migrations, models
import django.db.models.functions.text
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("drive", "0007_shareaccesslog"),
    ]

    operations = [
        migrations.CreateModel(
            name="WaitlistEntry",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "email",
                    models.EmailField(
                        db_index=True,
                        help_text="Submitter email (waitlist key, stored lower-cased).",
                        max_length=254,
                    ),
                ),
                ("name", models.CharField(blank=True, default="", max_length=200)),
                (
                    "interest",
                    models.TextField(
                        blank=True,
                        default="",
                        help_text="Free-text: what they want to use Drive for.",
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        blank=True,
                        default="drive_landing",
                        help_text="Which surface/CTA captured this (analytics).",
                        max_length=64,
                    ),
                ),
                (
                    "user_agent",
                    models.CharField(blank=True, default="", max_length=400),
                ),
                ("referrer", models.CharField(blank=True, default="", max_length=400)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("invited", "Invited"),
                            ("converted", "Converted"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name_plural": "Waitlist entries",
                "db_table": "drive_waitlist_entry",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="waitlistentry",
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower("email"),
                name="uq_waitlist_email_ci",
            ),
        ),
    ]
