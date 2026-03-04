from django.contrib import messages
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Case, When, IntegerField, DateField, DateTimeField, F, Min
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Product, Component, ProductComponent, Order, ProductBuild, StockMovement
from .forms import ProductBuildForm, ReceiveStockForm, AdjustStockForm, ShipOrderForm, CancelOrderForm
from .services.inventory import record_stock_movement

# Home page view
def home(request):
    return render(request, 'home.html')

@login_required
def orders_list(request):
    status_filter = request.GET.get("status", "")

    orders = Order.objects.all()

    if status_filter:
        orders = orders.filter(status=status_filter)

    orders = orders.annotate(
        status_order=Case(
            When(status="pending", then=0),
            When(status="building", then=1),
            When(status="completed", then=2),
            When(status="shipped", then=3),
            When(status="cancelled", then=4),
            default=99,
            output_field=IntegerField(),
        ),
        # earliest build start for this order (NULL if no builds)
        first_built_at=Min("productbuild__built_at"),
    ).annotate(
        # Building: sort by earliest build start (oldest first)
        build_sort_ts=Case(
            When(status="building", then=F("first_built_at")),
            default=None,
            output_field=DateTimeField(),
        ),
        # Non-building: keep newest orders first
        other_sort_ts=Case(
            When(status="building", then=None),
            default=F("order_date"),
            output_field=DateTimeField(),  # ok to use DateTimeField here; date will cast fine
        ),
    ).order_by(
        "status_order",
        "build_sort_ts",    # building oldest -> newest
        "-other_sort_ts",   # others newest -> oldest
        "-id",
    )

    return render(
        request,
        "orders/orders_list.html",
        {"orders": orders, "status_filter": status_filter},
    )

def component_list(request):
    components = Component.objects.all()
    return render(request, 'inventory/component_list.html', {'components': components})

def clean_quantity(self):
    qty = self.cleaned_data['quantity']
    if qty < 1:
        raise forms.ValidationError("Quantity must be at least 1.")
    return qty

def product_bom(request):
    product = get_object_or_404(Product, name="Vendetta")
    product_components = ProductComponent.objects.filter(product=product).select_related('component')
    buildable_units = get_max_buildable_units(product)
    recent_builds = ProductBuild.objects.filter(product=product).order_by('-built_at')[:5]
    # Only pending orders
    eligible_orders = Order.objects.filter(status__in=['pending', 'building'])

    # Instantiate the form and restrict the order field to pending orders
    form = ProductBuildForm(order_queryset=eligible_orders)

    return render(request, 'build/product_bom.html', {
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

    try:
        with transaction.atomic():
            build = ProductBuild.objects.create(product=product, order=order, quantity=quantity)
    
            for pc in product_components:
                qty_to_consume = pc.quantity_required * quantity
    
                record_stock_movement(
                    component_id=pc.component.id,
                    qty_delta=-qty_to_consume,  # ✅ consume stock
                    reason=StockMovement.Reason.BUILD_CONSUME,
                    user=request.user,
                    note=f"Consumed for build {build.id}",
                    ref=build,
                )
    
            # ✅ Update order status (keep inside the transaction)
            if order:
                order.status = 'building'
                order.save()
    
    except ValidationError as e:
        messages.error(request, str(e))
        return redirect('product_bom')

    messages.success(request, f"{quantity} unit(s) of {product.name} built successfully!")
    return redirect('product_bom')


@require_POST
def cancel_build(request, build_id):
    build = get_object_or_404(ProductBuild, id=build_id)
    product_components = ProductComponent.objects.filter(product=build.product)

    with transaction.atomic():
        for pc in product_components:
            qty_to_return = pc.quantity_required * build.quantity

            record_stock_movement(
                component_id=pc.component.id,
                qty_delta=qty_to_return,
                reason=StockMovement.Reason.BUILD_CANCEL_RETURN,
                user=request.user,
                note=f"Returned from cancelled build {build.id}",
                ref=build,
            )

        # If the build was linked to an order, revert its status to 'pending'
        print(f"Build ID {build.id} linked to Order: {build.order}, Status: {getattr(build.order, 'status', 'N/A')}")

        if build.order and build.order.status == 'building':
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
    return render(request, 'orders/order_list.html', {
        'orders': orders,
        'status_filter': status_filter,
    })

@login_required
def inventory_receive(request):
    if request.method == "POST":
        form = ReceiveStockForm(request.POST)
        if form.is_valid():
            component = form.cleaned_data["component"]
            qty = form.cleaned_data["quantity"]
            note = form.cleaned_data["note"]

            record_stock_movement(
                component_id=component.id,
                qty_delta=qty,
                reason=StockMovement.Reason.RECEIVE,
                user=request.user,
                note=note,
                ref=None,
            )
            messages.success(request, f"Received {qty} into {component}.")
            return redirect("component_ledger", id=component.id)
    else:
        form = ReceiveStockForm()

    return render(request, "inventory/receive.html", {"form": form})


@login_required
def inventory_adjust(request):
    if request.method == "POST":
        form = AdjustStockForm(request.POST)
        if form.is_valid():
            component = form.cleaned_data["component"]
            qty_delta = form.cleaned_data["qty_delta"]
            note = form.cleaned_data["note"]

            try:
                record_stock_movement(
                    component_id=component.id,
                    qty_delta=qty_delta,
                    reason=StockMovement.Reason.ADJUSTMENT,
                    user=request.user,
                    note=note,
                    ref=None,
                )
                messages.success(request, f"Adjusted {component} by {qty_delta}.")
                return redirect("component_ledger", id=component.id)
            except ValidationError as e:
                form.add_error(None, e)
    else:
        form = AdjustStockForm()

    return render(request, "inventory/adjust.html", {"form": form})


@login_required
def component_ledger(request, id):
    component = get_object_or_404(Component, pk=id)
    movements = (
        StockMovement.objects
        .filter(component=component)
        .select_related("created_by")
        .order_by("-created_at")[:50]
    )

    return render(
        request,
        "inventory/ledger.html",
        {"component": component, "movements": movements},
    )


@login_required
@require_POST
@transaction.atomic
def start_build(request, order_id):
    order = get_object_or_404(Order.objects.select_for_update(), id=order_id)

    if not order.can_start_build():
        messages.error(request, "Order cannot be started (must be Pending).")
        return redirect("orders_list")

    # Guard against double-clicks / retries
    existing_build = ProductBuild.objects.filter(order=order).first()
    if existing_build:
        messages.info(request, f"Build already exists for Order #{order.id}.")
        order.status = Order.Status.BUILDING
        order.save()
        return redirect("orders_list")

    # Create the build (you likely already know which product + qty this maps to)
    # Example assumes Order has product + quantity fields. Adjust to your schema.
    build = ProductBuild.objects.create(
        order=order,
        product=order.product,
        quantity=order.quantity,
    )

    # Reserve/consume components immediately (your chosen approach)
    for pc in ProductComponent.objects.filter(product=build.product):
        qty_delta = -(pc.quantity_required * build.quantity)

        record_stock_movement(
            component_id=pc.component.id,
            qty_delta=qty_delta,
            reason=StockMovement.Reason.BUILD_CONSUME,  # or RESERVE if you have it
            user=request.user,
            note=f"Order #{order.id} started build #{build.id}",
            ref=build,
        )

    order.status = Order.Status.BUILDING
    order.save()

    messages.success(request, f"Started build #{build.id} for Order #{order.id}.")
    return redirect("orders_list")


@login_required
def mark_shipped(request, order_id):
    order = get_object_or_404(Order, id=order_id)

    if request.method == "POST":
        form = ShipOrderForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                order = Order.objects.select_for_update().get(id=order_id)

                if not order.can_mark_shipped():
                    messages.error(request, "Order must be Building to mark shipped.")
                    return redirect("orders_list")

                order.shipping_date = form.cleaned_data["shipping_date"]
                order.tracking_number = form.cleaned_data["tracking_number"]
                order.status = Order.Status.SHIPPED
                order.save()

            messages.success(request, f"Order #{order.id} marked as shipped.")
            return redirect("orders_list")
    else:
        form = ShipOrderForm()

    return render(request, "orders/mark_shipped.html", {"order": order, "form": form})


@login_required
@require_POST
@transaction.atomic
def mark_complete(request, order_id):
    order = get_object_or_404(Order.objects.select_for_update(), id=order_id)

    if order.status != "building":
        messages.error(request, "Order must be Building to mark complete.")
        return redirect("orders_list")

    order.status = "completed"
    order.save()

    messages.success(request, f"Order #{order.id} marked as completed.")
    return redirect("orders_list")

@login_required
def ship_order(request, order_id):
    order = get_object_or_404(Order, id=order_id)

    if request.method == "POST":
        form = ShipOrderForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                order = Order.objects.select_for_update().get(id=order_id)

                if order.status != "completed":
                    messages.error(request, "Order must be Completed to ship.")
                    return redirect("orders_list")

                order.shipping_date = form.cleaned_data["shipping_date"]
                order.shipping_tracking_number = form.cleaned_data["tracking_number"] or ""
                order.status = "shipped"
                order.save()

            messages.success(request, f"Order #{order.id} shipped.")
            return redirect("orders_list")
    else:
        form = ShipOrderForm(initial={
            "shipping_date": timezone.now().date(),
            "tracking_number": order.shipping_tracking_number,
        })

    return render(request, "orders/ship_order.html", {"order": order, "form": form})

@login_required
def cancel_order(request, order_id):
    order = get_object_or_404(Order, id=order_id)

    if request.method == "POST":
        form = CancelOrderForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                order = Order.objects.select_for_update().get(id=order_id)

                if not order.can_cancel():
                    messages.error(request, "Only Pending/Building orders can be cancelled.")
                    return redirect("orders_list")

                # If there is a build, cancel it and return stock via your existing cancel_build logic
                build = ProductBuild.objects.filter(order=order).first()
                if build:
                    # Inline your cancel_build logic OR call a shared service function
                    for pc in ProductComponent.objects.filter(product=build.product):
                        qty_to_return = pc.quantity_required * build.quantity
                        record_stock_movement(
                            component_id=pc.component.id,
                            qty_delta=qty_to_return,
                            reason=StockMovement.Reason.BUILD_CANCEL_RETURN,
                            user=request.user,
                            note=f"Returned from cancelled build {build.id} (Order #{order.id})",
                            ref=build,
                        )
                    build.delete()

                order.status = Order.Status.CANCELLED
                order.cancelled_at = timezone.now()
                order.cancellation_reason = form.cleaned_data["reason"] or ""
                order.save()

            messages.success(request, f"Order #{order.id} cancelled safely.")
            return redirect("orders_list")
    else:
        form = CancelOrderForm()

    return render(request, "orders/cancel_order.html", {"order": order, "form": form})
