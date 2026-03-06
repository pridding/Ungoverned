from django.db import models
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify
from django.core.validators import MinLengthValidator

class Customer(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    address = models.TextField(blank=True)
    phone_number = models.CharField(max_length=50, blank=True)
    country = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class Supplier(models.Model):
    name = models.CharField(max_length=255)
    contact_info = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name

class Component(models.Model):
    PRODUCTION_METHOD_CHOICES = [
        ('in_house', 'In House'),
        ('outsourced', 'Outsourced'),
        ('purchased', 'Purchased'),
    ]

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    unit = models.CharField(max_length=50)
    cost_per_unit = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    stock_quantity = models.IntegerField(default=0, editable=False)
    production_method = models.CharField(max_length=20, choices=PRODUCTION_METHOD_CHOICES)
    notes = models.TextField(blank=True, null=True)
    suppliers = models.ManyToManyField(Supplier, through='SupplierComponent', related_name='components')
    low_stock_threshold = models.IntegerField(default=10)  

    def __str__(self):
        return self.name

    def is_low_stock(self):  # <-- and added this method
        return self.stock_quantity < self.low_stock_threshold

    def stock_level_status(self):
        if self.stock_quantity == 0:
            return "danger"   # red
        elif self.stock_quantity <= self.low_stock_threshold:
            return "warning"  # yellow
        else:
            return "success"  # green

# ungoverned_app/models.py
from django.core.validators import MinLengthValidator

class StockMovement(models.Model):
    class Reason(models.TextChoices):
        RECEIVE = "RECEIVE", "Receive"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"
        BUILD_CONSUME = "BUILD_CONSUME", "Build Consume"
        BUILD_CANCEL_RETURN = "BUILD_CANCEL_RETURN", "Build Cancel Return"
        ORDER_CANCEL_RETURN = "ORDER_CANCEL_RETURN", "Order Cancel Return"  # later

    component = models.ForeignKey("Component", on_delete=models.CASCADE, related_name="stock_movements")
    qty_delta = models.IntegerField()
    reason = models.CharField(max_length=32, choices=Reason.choices)

    note = models.TextField(blank=True)

    reference_type = models.CharField(max_length=64, blank=True)  # e.g. “ProductBuild”, “Order”, “Manual”
    reference_id = models.IntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements_created",
    )

    class Meta:
        indexes = [
            models.Index(fields=["component", "-created_at"]),
            models.Index(fields=["reason", "-created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.component} {self.qty_delta} ({self.reason})"

class SupplierComponent(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE)
    component = models.ForeignKey(Component, on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.supplier.name} supplies {self.component.name}"


class Product(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    components = models.ManyToManyField("Component", through='ProductComponent')

    def __str__(self):
        return self.name


class ProductComponent(models.Model):
    product = models.ForeignKey('Product', on_delete=models.CASCADE)
    component = models.ForeignKey('Component', on_delete=models.CASCADE)
    quantity_required = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f"{self.quantity_required} x {self.component.name} for {self.product.name}"

class Order(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("building", "Building"),
        ("shipped", "Shipped"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE)
    products = models.ManyToManyField(Product, through="OrderItem")

    order_date = models.DateField()
    shipping_date = models.DateField(blank=True, null=True)
    shipping_tracking_number = models.CharField(max_length=100, blank=True)
    warranty_months = models.IntegerField(default=12)

    # ✅ add default so new orders don't need status manually
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )

    # ✅ optional but very useful for audit/history
    cancelled_at = models.DateTimeField(blank=True, null=True)
    cancellation_reason = models.TextField(blank=True, default="")
    notes = models.TextField(blank=True, default="")  # general notes for anything

    def __str__(self):
        return f"Order #{self.id} for {self.customer}"

    def warranty_expires_on(self):
        if self.shipping_date:
            return self.shipping_date + timezone.timedelta(days=self.warranty_months * 30)
        return None

    # ✅ convenience helpers for UI/buttons
    def can_start_build(self):
        return self.status == "pending"
    
    def can_mark_complete(self):
        return self.status == "building"
    
    def can_ship(self):
        return self.status == "completed"
    
    def can_cancel(self):
        return self.status in {"pending", "building"}

class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='order_items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.IntegerField()

    def __str__(self):
        return f"{self.quantity} x {self.product.name} for Order #{self.order.id}"

class ProductOption(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='product_options')
    option_type = models.CharField(max_length=100)
    option_value = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.option_type}: {self.option_value} ({self.product.name})"

class ProductBuild(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    built_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        order_id = self.order.id if self.order else "N/A"
        return f"{self.quantity} x {self.product.name} for Order #{order_id}"
