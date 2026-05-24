from django.db import models


class LegacyOrder(models.Model):
    """
    Simulates legacy, denormalized order data.
    Contains a JSONField with all order details.
    """
    external_id = models.CharField(max_length=100, unique=True, db_index=True)
    raw_data = models.JSONField()
    migrated = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'orders_legacyorder'
        ordering = ['external_id']
        indexes = [
            models.Index(fields=['migrated', 'external_id']),
        ]

    def __str__(self):
        return f"LegacyOrder({self.external_id})"


class Order(models.Model):
    """
    New normalized order table.
    Stores top-level order information.
    """
    external_id = models.CharField(max_length=100, unique=True, db_index=True)
    customer_email = models.EmailField(db_index=True)
    total_price = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'orders_order'

    def __str__(self):
        return f"Order({self.external_id}, {self.customer_email})"


class OrderLine(models.Model):
    """
    Normalized line items for each order.
    Linked to Order via ForeignKey.
    """
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='lines')
    sku = models.CharField(max_length=100)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        db_table = 'orders_orderline'
        constraints = [
            models.UniqueConstraint(
                fields=['order', 'sku', 'quantity', 'unit_price'],
                name='uniq_orderline_identity',
            ),
        ]

    def __str__(self):
        return f"OrderLine({self.order_id}, {self.sku})"
