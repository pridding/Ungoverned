from django.contrib import admin
from .models import Customer, Supplier, Component, SupplierComponent, ProductComponent, Order, OrderItem, ProductOption, StockMovement

class ProductComponentInline(admin.TabularInline):
    model = ProductComponent
    extra = 1  # How many empty forms to show by default
    autocomplete_fields = ['component']  # Nice searchable dropdown
    fields = ('component', 'quantity_required')  # Only show these fields

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'country', 'last_order_date')
    search_fields = ('name',)

    def last_order_date(self, obj):
        latest_order = obj.order_set.order_by('-order_date').first()
        return latest_order.order_date if latest_order else None
    last_order_date.short_description = 'Last Order Date'

@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)


@admin.register(SupplierComponent)
class SupplierComponentAdmin(admin.ModelAdmin):
    list_display = ('supplier', 'component')
    search_fields = ('supplier__name', 'component__name')


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('customer', 'order_date', 'status')
    list_filter = ('status',)
    search_fields = ('customer__name',)


@admin.register(ProductOption)
class ProductOptionAdmin(admin.ModelAdmin):
    list_display = ('product', 'option_type', 'option_value')
    search_fields = ('product__name', 'option_type', 'option_value')

@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("created_at", "component", "qty_delta", "reason", "reference_type", "reference_id", "created_by")
    list_filter = ("reason", "component")
    search_fields = ("component__name", "note", "reference_type")

class StockMovementInline(admin.TabularInline):
    model = StockMovement
    extra = 0
    can_delete = False
    ordering = ("-created_at",)
    fields = ("created_at", "qty_delta", "reason", "reference_type", "reference_id", "created_by", "note")
    readonly_fields = fields
    show_change_link = True

@admin.register(Component)
class ComponentAdmin(admin.ModelAdmin):
    list_display = ('name', 'unit', 'stock_quantity', 'production_method', 'is_low_stock')
    list_filter = ('production_method',)
    search_fields = ('name',)
    readonly_fields = ("stock_quantity",)
    inlines = [StockMovementInline]
