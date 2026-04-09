from django.db import models
from django.db.models import ExpressionWrapper, DecimalField, Case, When, Value, IntegerField, F, Max
from django.db.models.functions import Coalesce
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify
from django.urls import reverse
from django.core.validators import MinLengthValidator
from decimal import Decimal


def with_legacy_low_stock_threshold(queryset):
    return queryset.annotate(
        legacy_low_stock_threshold=ExpressionWrapper(
            F("qty_per_vehicle") * 3,
            output_field=DecimalField(max_digits=10, decimal_places=2),
        )
    )

def with_stock_priority(queryset):
    return queryset.annotate(
        stock_priority=Case(
            When(stock_quantity=0, then=Value(0)),
            When(stock_quantity__lte=F("bom_low_stock_threshold"), then=Value(1)),
            default=Value(2),
            output_field=IntegerField(),
        )
    )

def with_bom_low_stock_threshold(queryset):
    return queryset.annotate(
        max_bom_quantity_required=Coalesce(
            Max("productcomponent__quantity_required"),
            Value(0),
        ),
    ).annotate(
        bom_low_stock_threshold=ExpressionWrapper(
            F("max_bom_quantity_required") * 3,
            output_field=DecimalField(max_digits=10, decimal_places=2),
        )
    )

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

    top_level_item = models.CharField(max_length=150, blank=True, null=True)
    sub_assembly = models.CharField(max_length=150, blank=True, null=True)
    material = models.CharField(max_length=100, blank=True, null=True)

    unit = models.CharField(max_length=50)
    cost_per_unit = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    stock_quantity = models.IntegerField(
        default=0,
        editable=False,
        help_text="Managed automatically via stock movements. Use Receive Stock / Adjust Stock, not direct edits."
    )
    production_method = models.CharField(max_length=20, choices=PRODUCTION_METHOD_CHOICES)
    notes = models.TextField(blank=True, null=True)
    suppliers = models.ManyToManyField(Supplier, through='SupplierComponent', related_name='components')

    def __str__(self):
        return self.name
    
    @property
    def low_stock_threshold(self):
        max_required = self.productcomponent_set.aggregate(
            max_qty=Max("quantity_required")
        )["max_qty"]
    
        if max_required is None:
            return None
    
        return Decimal(max_required) * Decimal("3")
    
    def is_low_stock(self):
        threshold = self.low_stock_threshold
        if threshold is None:
            return False
        return self.stock_quantity <= threshold

    def stock_level_status(self):
        threshold = self.low_stock_threshold
        if self.stock_quantity == 0:
            return "danger"
        elif threshold is not None and self.stock_quantity <= threshold:
            return "warning"
        else:
            return "success"
    
class StockMovement(models.Model):

    class Reason(models.TextChoices):
        RECEIVE = "RECEIVE", "Receive"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"
        BUILD_CONSUME = "BUILD_CONSUME", "Build Consume"
        BUILD_CANCEL_RETURN = "BUILD_CANCEL_RETURN", "Build Cancel Return"
        ORDER_CANCEL_RETURN = "ORDER_CANCEL_RETURN", "Order Cancel Return"

    component = models.ForeignKey(
        "Component",
        on_delete=models.CASCADE,
        related_name="stock_movements"
    )

    qty_delta = models.IntegerField()

    reason = models.CharField(
        max_length=32,
        choices=Reason.choices
    )

    note = models.TextField(blank=True)

    reference_type = models.CharField(
        max_length=64,
        blank=True
    )  # e.g. “ProductBuild”, “Order”, “Manual”

    reference_id = models.IntegerField(
        null=True,
        blank=True
    )

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

    # ---------------------------------------------------------
    # Helper Properties (NEW)
    # ---------------------------------------------------------

    @property
    def qty_display(self):
        """Formatted quantity change (+/-)."""
        if self.qty_delta > 0:
            return f"+{self.qty_delta}"
        return str(self.qty_delta)

    @property
    def reference_object(self):
        """
        Attempts to resolve the referenced object.
        Uses reference_type + reference_id.
        """
        if not self.reference_type or not self.reference_id:
            return None

        try:
            if self.reference_type == "ProductBuild":
                from .models import ProductBuild
                return ProductBuild.objects.filter(id=self.reference_id).first()

            if self.reference_type == "Order":
                from .models import Order
                return Order.objects.filter(id=self.reference_id).first()

        except Exception:
            return None

        return None

    @property
    def reference_label(self):
        """Readable reference label for UI display."""
        if self.reference_type == "ProductBuild":
            return f"Build #{self.reference_id}"

        if self.reference_type == "Order":
            return f"Order #{self.reference_id}"

        if self.reference_type:
            return f"{self.reference_type} #{self.reference_id}"

        return "-"

    @property
    def reference_url(self):
        """
        Returns a URL for the referenced object if a page exists.
        Safe to use in templates.
        """
        if self.reference_type == "Order" and self.reference_id:
            try:
                return reverse("order_detail", args=[self.reference_id])
            except Exception:
                return None

        # Only add if you create a Build detail page later
        if self.reference_type == "ProductBuild" and self.reference_id:
            return None

        return None

class SupplierComponent(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE)
    component = models.ForeignKey(Component, on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.supplier.name} supplies {self.component.name}"


class Product(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    components = models.ManyToManyField("Component", through='ProductComponent')

    from decimal import Decimal

    @property
    def cost_per_vehicle(self):
        total = Decimal("0")
        for bom_item in self.productcomponent_set.select_related("component").all():
            if bom_item.component.cost_per_unit is not None:
                total += bom_item.component.cost_per_unit * bom_item.quantity_required
        return total

    def __str__(self):
        return self.name

class ProductComponent(models.Model):
    product = models.ForeignKey("Product", on_delete=models.CASCADE)
    component = models.ForeignKey("Component", on_delete=models.CASCADE)
    quantity_required = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "component"],
                name="unique_product_component",
            )
        ]

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
