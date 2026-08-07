from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="DecisionArchivee",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("tx_id", models.CharField(db_index=True, max_length=32, unique=True)),
                ("msisdn", models.CharField(db_index=True, max_length=20)),
                ("destinataire", models.CharField(max_length=20)),
                ("montant", models.BigIntegerField()),
                ("score", models.IntegerField()),
                ("decision", models.CharField(db_index=True, max_length=10)),
                ("regles", models.CharField(blank=True, max_length=100)),
                ("latence_ms", models.FloatField()),
                ("instance", models.CharField(max_length=20)),
                ("horodatage", models.DateTimeField(db_index=True)),
                ("archivee_le", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-horodatage"]},
        ),
        migrations.AddIndex(
            model_name="decisionarchivee",
            index=models.Index(fields=["msisdn", "horodatage"],
                               name="scoring_dec_msisdn_ts_idx"),
        ),
    ]
