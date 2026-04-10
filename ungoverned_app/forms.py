from django import forms
from django.utils import timezone
from django.core.exceptions import ValidationError
from .models import ProductBuild, Order, Component, with_bom_low_stock_threshold, with_stock_priority
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
    
        base_qs = Component.objects.all()
    
        self.fields["component"].queryset = (
            with_stock_priority(
                with_bom_low_stock_threshold(base_qs)
            ).order_by("stock_priority", "name")
        )
    
        self.fields["component"].label_from_instance = (
            lambda obj: f"{obj.name} (Stock: {int(obj.stock_quantity)})"
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
    
        base_qs = Component.objects.all()

        self.fields["component"].queryset = (
            with_stock_priority(
                with_bom_low_stock_threshold(base_qs)
            )
            .order_by("stock_priority", "name")
        )

        self.fields["component"].label_from_instance = (
            lambda obj: f"{obj.name} (Stock: {int(obj.stock_quantity)})"
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
