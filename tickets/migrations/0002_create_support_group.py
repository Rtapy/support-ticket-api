from django.db import migrations


def create_support_group(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.using(schema_editor.connection.alias).get_or_create(name='Support')


class Migration(migrations.Migration):
    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('tickets', '0001_initial'),
    ]

    operations = [
        # Reversing this migration must not discard user memberships or permissions.
        migrations.RunPython(create_support_group, reverse_code=migrations.RunPython.noop),
    ]
