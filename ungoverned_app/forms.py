from django import forms
from django.utils import timezone
from django.core.exceptions import ValidationError
from .models import ProductBuild, Order, Component


from django import forms
from .models import ProductBuild, Order

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
    component = forms.ModelChoiceField(queryset=Component.objects.all())
    quantity = forms.IntegerField(min_value=1)
    note = forms.CharField(widget=forms.Textarea, required=False)

class AdjustStockForm(forms.Form):
    component = forms.ModelChoiceField(queryset=Component.objects.all())
    qty_delta = forms.IntegerField()  # can be +/-; 0 not allowed
    note = forms.CharField(widget=forms.Textarea, required=True)

    def clean_qty_delta(self):
        v = self.cleaned_data["qty_delta"]
        if v == 0:
            raise ValidationError("Adjustment cannot be 0.")
        return v

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
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
