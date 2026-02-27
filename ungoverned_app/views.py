from django.contrib import messages
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_POST
from .models import Product, Component, ProductComponent, Order, ProductBuild
from .forms import ProductBuildForm


# Home page view
def home(request):
    return render(request, 'home.html')

def component_list(request):
    components = Component.objects.all()
    return render(request, 'ungoverned_app/component_list.html', {'components': components})

def product_bom(request):
    product = get_object_or_404(Product, name="Vendetta")
    product_components = ProductComponent.objects.filter(product=product).select_related('component')
    buildable_units = get_max_buildable_units(product)
    recent_builds = ProductBuild.objects.filter(product=product).order_by('-built_at')[:5]
    # Only pending orders
    pending_orders = Order.objects.filter(status='pending')

    # Instantiate the form and restrict the order field to pending orders
    form = ProductBuildForm()
    form.fields['order'].queryset = pending_orders

    return render(request, 'ungoverned_app/product_bom.html', {
        'product': product,
        'product_components': product_components,
        'buildable_units': buildable_units,
        'recent_builds': recent_builds,
        'form': form,
    })

@require_POST
def build_product(request):
    product = get_object_or_404(Product, name="Vendetta")
    form = ProductBuildForm(request.POST)

    if not form.is_valid():
        messages.error(request, "Invalid build request.")
        return redirect('product_bom')

    quantity = form.cleaned_data['quantity']
    order = form.cleaned_data['order']

    product_components = ProductComponent.objects.filter(product=product)
    insufficient = [pc.component.name for pc in product_components
                    if pc.component.stock_quantity < pc.quantity_required * quantity]

    if insufficient:
        messages.error(request, f"Cannot build product. Insufficient stock for: {', '.join(insufficient)}.")
        return redirect('product_bom')

    for pc in product_components:
        pc.component.stock_quantity -= pc.quantity_required * quantity
        pc.component.save()

    ProductBuild.objects.create(product=product, order=order, quantity=quantity)
    messages.success(request, f"{quantity} unit(s) of {product.name} built successfully!")

    # ✅ Update order status
    if order:
        order.status = 'building'
        order.save()

    return redirect('product_bom')


@require_POST
def cancel_build(request, build_id):
    build = get_object_or_404(ProductBuild, id=build_id)
    product_components = ProductComponent.objects.filter(product=build.product)

    for pc in product_components:
        pc.component.stock_quantity += pc.quantity_required * build.quantity
        pc.component.save()

    # If the build was linked to an order, revert its status to 'pending'
    print(f"Build ID {build.id} linked to Order: {build.order}, Status: {getattr(build.order, 'status', 'N/A')}")

    if build.order:
        if build.order.status == 'building':
            build.order.status = 'pending'
            build.order.save()

    build.delete()
    messages.success(request, f"Cancelled build of {build.quantity} unit(s) of {build.product.name}.")
    return redirect('product_bom')


def get_max_buildable_units(product):
    product_components = ProductComponent.objects.filter(product=product)
    if not product_components:
        return 0
    return min(pc.component.stock_quantity // pc.quantity_required for pc in product_components)

def order_list(request):
    status_filter = request.GET.get('status')
    orders = Order.objects.select_related('customer')

    if status_filter:
        orders = orders.filter(status=status_filter)

    orders = orders.order_by('-order_date')
    return render(request, 'ungoverned_app/order_list.html', {
        'orders': orders,
        'status_filter': status_filter,
    })
