from django import forms
from .models import ProductBuild, Order

class ProductBuildForm(forms.ModelForm):
    class Meta:
        model = ProductBuild
        fields = ['quantity', 'order']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Customize order dropdown to show customer name and order ID
        self.fields['order'].queryset = Order.objects.filter(status='pending')
        self.fields['order'].label_from_instance = lambda obj: f"{obj.customer} (Order #{obj.id})"
