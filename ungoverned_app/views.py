from django.contrib import messages
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.urls import reverse
from django.db.models import Case, When, IntegerField, DateField, DateTimeField, F, Min, Sum
from django.db.models.functions import Coalesce
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Product, Component, ProductComponent, Order, OrderItem, ProductBuild, StockMovement
from .forms import ProductBuildForm, ReceiveStockForm, AdjustStockForm, ShipOrderForm, CancelOrderForm, OrderNotesForm
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
    components = Component.objects.all().prefetch_related("suppliers")
    return render(request, "inventory/component_list.html", {"components": components})

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

    preselect_order_id = request.GET.get("order")

    # ✅ ONLY pending orders are selectable
    eligible_orders = Order.objects.filter(status="pending").select_related("customer")

    selected_order = None
    if preselect_order_id:
        # ✅ only preselect if the order is pending
        selected_order = eligible_orders.filter(id=preselect_order_id).first()
        if not selected_order:
            messages.warning(request, "That order is not Pending, so it can't be started.")

    form = ProductBuildForm(
        order_queryset=eligible_orders,
        initial={"order": selected_order} if selected_order else None,
    )

    return render(request, 'build/product_bom.html', {
        'product': product,
        'product_components': product_components,
        'buildable_units': buildable_units,
        'recent_builds': recent_builds,
        'form': form,
        'selected_order': selected_order,
    })

@login_required
def build_product_for_order(request, order_id):
    order = get_object_or_404(Order, id=order_id)

    if order.status != "pending":
        messages.error(request, "This order is not Pending, so it can't be started.")
        return redirect("orders_list")

    # Load the order items (your through model has related_name='order_items')
    items = order.order_items.select_related("product").all()

    if not items.exists():
        messages.error(request, "This order has no items to build.")
        return redirect("orders_list")

    # ====== GET: show inventory check ======
    if request.method == "GET":
        # Build a simple “shortages” structure to display.
        # This assumes Component has a stock/quantity field available; adjust to your actual stock field.
        lines = []
        for item in items:
            product = item.product
            qty = item.quantity

            bom = ProductComponent.objects.filter(product=product).select_related("component")
            bom_lines = []
            for pc in bom:
                required = pc.quantity_required * qty
                on_hand = getattr(pc.component, "current_stock", None)  # <-- change to your actual stock field
                shortage = None if on_hand is None else max(0, required - on_hand)
                bom_lines.append({
                    "component": pc.component,
                    "required": required,
                    "on_hand": on_hand,
                    "shortage": shortage,
                })

            lines.append({
                "product": product,
                "qty": qty,
                "bom_lines": bom_lines,
            })

        return render(request, "orders/build_for_order.html", {
            "order": order,
            "items": items,
            "lines": lines,
        })

    # ====== POST: actually perform the build ======
    with transaction.atomic():
        order = Order.objects.select_for_update().get(id=order_id)
        if order.status != "pending":
            messages.error(request, "Order is no longer Pending.")
            return redirect("orders_list")

        # create build rows + consume stock
        for item in order.order_items.select_related("product").all():
            build = ProductBuild.objects.create(
                order=order,
                product=item.product,
                quantity=item.quantity,
                # built_at auto_now_add handles timestamp
            )

            for pc in ProductComponent.objects.filter(product=item.product):
                qty_delta = -(pc.quantity_required * item.quantity)

                record_stock_movement(
                    component_id=pc.component.id,
                    qty_delta=qty_delta,
                    reason=StockMovement.Reason.BUILD_CONSUME,  # use your actual reason enum
                    user=request.user,
                    note=f"Order #{order.id} started build #{build.id}",
                    ref=build,
                )

        order.status = "building"
        order.save()

    messages.success(request, f"Order #{order.id} moved to Building.")
    return redirect("orders_list")

@require_POST
@login_required
def build_product(request):
    product = get_object_or_404(Product, name="Vendetta")
    form = ProductBuildForm(request.POST)

    # Pull the raw order id early so we can preserve it on redirects even if form invalid
    order_id = request.POST.get("order") or ""

    def bom_redirect():
        url = reverse("product_bom")
        return redirect(f"{url}?order={order_id}" if order_id else url)

    if not form.is_valid():
        messages.error(request, "Invalid build request.")
        return bom_redirect()

    quantity = form.cleaned_data["quantity"]
    order = form.cleaned_data["order"]  # may be None

    product_components = ProductComponent.objects.filter(product=product).select_related("component")

    insufficient = [
        pc.component.name
        for pc in product_components
        if pc.component.stock_quantity < (pc.quantity_required * quantity)
    ]

    if insufficient:
        messages.error(
            request,
            f"Cannot build product. Insufficient stock for: {', '.join(insufficient)}."
        )
        return bom_redirect()

    try:
        with transaction.atomic():
            # Lock the order row if present (prevents race/double clicks)
            if order:
                order = Order.objects.select_for_update().get(id=order.id)

            build = ProductBuild.objects.create(product=product, order=order, quantity=quantity)

            for pc in product_components:
                qty_to_consume = pc.quantity_required * quantity

                record_stock_movement(
                    component_id=pc.component.id,
                    qty_delta=-qty_to_consume,
                    reason=StockMovement.Reason.BUILD_CONSUME,
                    user=request.user,
                    note=f"Consumed for build {build.id}",
                    ref=build,
                )

            # ✅ Only move Pending -> Building (don’t overwrite other states)
            if order and order.status == "pending":
                order.status = "building"
                order.save()

    except ValidationError as e:
        messages.error(request, str(e))
        return bom_redirect()

    messages.success(request, f"{quantity} unit(s) of {product.name} built successfully!")
    return bom_redirect()

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
def start_build(request, order_id):
    order = get_object_or_404(Order, id=order_id)

    if order.status != "pending":
        messages.error(request, "Only Pending orders can be started.")
        return redirect("orders_list")

    return redirect(f"{reverse('product_bom')}?order={order.id}")

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
    has_build = ProductBuild.objects.filter(order=order).exists()

    if request.method == "POST":
        form = CancelOrderForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                order = Order.objects.select_for_update().get(id=order_id)

                if not order.can_cancel():
                    messages.error(request, "Only Pending/Building orders can be cancelled.")
                    return redirect("orders_list")

                build = ProductBuild.objects.filter(order=order).first()

                if build:
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

                order.status = "cancelled"
                order.cancelled_at = timezone.now()
                order.cancellation_reason = form.cleaned_data["reason"] or ""
                now = timezone.localtime(timezone.now())
                user_label = getattr(request.user, "get_username", lambda: str(request.user))()
                reason = (form.cleaned_data.get("reason") or "").strip() or "(no reason provided)"

                audit_line = f"{now:%Y-%m-%d %H:%M} - Cancelled by {user_label}. Reason: {reason}"
                order.notes = (order.notes.rstrip() + "\n" + audit_line) if order.notes else audit_line
                order.save()

            messages.success(request, f"Order #{order.id} cancelled safely.")
            return redirect("orders_list")

    else:
        form = CancelOrderForm()

    return render(
        request,
        "orders/cancel_order.html",
        {
            "order": order,
            "form": form,
            "has_build": has_build,
        },
    )

@login_required
@login_required
def order_detail(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    build = ProductBuild.objects.filter(order=order).order_by("built_at").first()
    items = OrderItem.objects.filter(order=order).select_related("product")

    if request.method == "POST":
        form = OrderNotesForm(request.POST, instance=order)
        if form.is_valid():
            form.save()
            messages.success(request, f"Saved notes for Order #{order.id}.")
            return redirect("order_detail", order_id=order.id)
    else:
        form = OrderNotesForm(instance=order)

    return render(
        request,
        "orders/order_detail.html",
        {
            "order": order,
            "form": form,
            "items": items,
            "build": build,
        },
    )

@login_required
@require_POST
@transaction.atomic
def reopen_order(request, order_id):
    order = get_object_or_404(Order.objects.select_for_update(), id=order_id)

    if order.status != "cancelled":
        messages.error(request, "Only cancelled orders can be reopened.")
        return redirect("orders_list")

    # Safety: don't reopen if there is still a build object linked
    if ProductBuild.objects.filter(order=order).exists():
        messages.error(request, "This order still has a build record. Cancel the build first.")
        return redirect("order_detail", order_id=order.id)

    now = timezone.localtime(timezone.now())
    user_label = getattr(request.user, "get_username", lambda: str(request.user))()

    # Append audit line to notes
    audit_line = f"{now:%Y-%m-%d %H:%M} - Reopened from CANCELLED to PENDING by {user_label}"
    if order.notes:
        order.notes = order.notes.rstrip() + "\n" + audit_line
    else:
        order.notes = audit_line

    order.status = "pending"
    order.save(update_fields=["status", "notes"])

    messages.success(request, f"Order #{order.id} reopened and moved back to Pending.")
    return redirect("orders_list")
