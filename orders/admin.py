from django.contrib import admin
from .models import LegacyOrder, Order, OrderLine


@admin.register(LegacyOrder)
class LegacyOrderAdmin(admin.ModelAdmin):
    list_display = ['external_id', 'migrated', 'created_at']
    list_filter = ['migrated']
    search_fields = ['external_id']


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['external_id', 'customer_email', 'total_price', 'created_at']
    search_fields = ['external_id', 'customer_email']


@admin.register(OrderLine)
class OrderLineAdmin(admin.ModelAdmin):
    list_display = ['order', 'sku', 'quantity', 'unit_price']
    search_fields = ['sku']
