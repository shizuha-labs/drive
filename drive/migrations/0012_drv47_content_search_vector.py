# Generated for DRV-47 content_text cache and trigger-maintained search vector.
from django.contrib.postgres.search import SearchVectorField
from django.db import migrations, models


FILE_SEARCH_VECTOR_SQL = """
CREATE OR REPLACE FUNCTION drive_file_search_vector_update()
RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('simple', coalesce(NEW.name, '')), 'A') ||
        setweight(to_tsvector('simple', concat_ws(' ', coalesce(NEW.extension, ''), coalesce(NEW.file_type, ''))), 'B') ||
        setweight(to_tsvector('simple', coalesce(NEW.content_text, '')), 'D');
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS drive_file_search_vector_update ON drive_file;
CREATE TRIGGER drive_file_search_vector_update
BEFORE INSERT OR UPDATE OF name, extension, file_type, content_text
ON drive_file
FOR EACH ROW EXECUTE FUNCTION drive_file_search_vector_update();
"""

FILE_SEARCH_VECTOR_REVERSE_SQL = """
DROP TRIGGER IF EXISTS drive_file_search_vector_update ON drive_file;
DROP FUNCTION IF EXISTS drive_file_search_vector_update();
"""


def create_file_search_vector_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(FILE_SEARCH_VECTOR_SQL)


def drop_file_search_vector_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(FILE_SEARCH_VECTOR_REVERSE_SQL)


class Migration(migrations.Migration):

    dependencies = [
        ('drive', '0011_drv36_activity_schema'),
    ]

    operations = [
        migrations.AddField(
            model_name='file',
            name='content_text',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='file',
            name='search_vector',
            field=SearchVectorField(blank=True, null=True),
        ),
        migrations.RunPython(
            create_file_search_vector_trigger,
            drop_file_search_vector_trigger,
        ),
    ]
