from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0001_initial'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='orderline',
            constraint=models.UniqueConstraint(
                fields=('order', 'sku', 'quantity', 'unit_price'),
                name='uniq_orderline_identity',
            ),
        ),
    ]
