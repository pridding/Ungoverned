from django import forms
from django.utils import timezone
from django.core.exceptions import ValidationError
from .models import ProductBuild, Order, Component
from django.db.models import Case, When, Value, IntegerField, F

class ProductBuildForm(forms.ModelForm):
    class Meta:
        model = ProductBuild
        fields = ["quantity", "order"]

    def __init__(self, *args, order_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)

        if order_queryset is None:
            order_queryset = Order.objects.filter(status="pending")

        self.fields["order"].queryset = order_queryset
        self.fields["order"].required = False
        self.fields["order"].empty_label = "— No order —"

        self.fields["order"].label_from_instance = (
            lambda obj: f"{obj.customer} (Order #{obj.id})"
        )

class ReceiveStockForm(forms.Form):
    component = forms.ModelChoiceField(
        queryset=Component.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"})
    )
    quantity = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "form-control"})
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3})
    )

    def __init__(self, *args, selected_component=False, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["component"].queryset = (
            Component.objects
            .annotate(
                stock_priority=Case(
                    When(stock_quantity=0, then=Value(0)),
                    When(stock_quantity__lte=F("qty_per_vehicle") * 3, then=Value(1)),
                    default=Value(2),
                    output_field=IntegerField(),
                )
            )
            .order_by("stock_priority", "name")
        )

        if selected_component:
            self.fields["quantity"].widget.attrs["autofocus"] = "autofocus"

class AdjustStockForm(forms.Form):
    component = forms.ModelChoiceField(
        queryset=Component.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"})
    )

    qty_delta = forms.IntegerField(
        widget=forms.NumberInput(attrs={"class": "form-control"})
    )

    note = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3})
    )

    def __init__(self, *args, selected_component=False, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["component"].queryset = (
            Component.objects
            .annotate(
                stock_priority=Case(
                    When(stock_quantity=0, then=Value(0)),
                    When(stock_quantity__lte=F("qty_per_vehicle") * 3, then=Value(1)),
                    default=Value(2),
                    output_field=IntegerField(),
                )
            )
            .order_by("stock_priority", "name")
        )

        if selected_component:
            self.fields["qty_delta"].widget.attrs["autofocus"] = "autofocus"

class ShipOrderForm(forms.Form):
    shipping_date = forms.DateField(
        initial=timezone.now().date(),
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"})
    )
    tracking_number = forms.CharField(
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Enter tracking number"
        })
    )

class CancelOrderForm(forms.Form):
    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"})
    )

class OrderNotesForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ["notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 5, "class": "form-control"}),
        }
