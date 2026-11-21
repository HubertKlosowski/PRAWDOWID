from django.db import migrations
from pgvector.django import VectorField


class Migration(migrations.Migration):
    dependencies = [("verify", "0001_initial")]

    operations = [
        migrations.RunSQL(
            """
            ALTER TABLE verify_chunk
                ALTER COLUMN embedding DROP NOT NULL,
                ALTER COLUMN embedding TYPE vector(1024) USING NULL::vector(1024)
            """,
            state_operations=[
                migrations.AlterField(
                    model_name="chunk",
                    name="embedding",
                    field=VectorField(dimensions=1024, null=True),
                ),
            ],
        ),
    ]
