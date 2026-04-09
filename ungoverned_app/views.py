from django.contrib import messages
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.urls import reverse
from django.db.models import Case, When, IntegerField, DateField, DateTimeField, F, Min, Sum, ExpressionWrapper, DecimalField, Value
from django.db.models.functions import Coalesce
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Product, Component, ProductComponent, Order, OrderItem, ProductBuild, StockMovement, with_legacy_low_stock_threshold, with_stock_priority, with_bom_low_stock_threshold
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
    base_qs = Component.objects.all().prefetch_related("suppliers")

    components = (
        with_stock_priority(
            with_bom_low_stock_threshold(base_qs)
        )
        .order_by(
            "top_level_item",
            "stock_priority",
            "sub_assembly",
            "stock_quantity",
            "name",
        )
    )

    return render(request, "inventory/component_list.html", {"components": components})

def clean_quantity(self):
    qty = self.cleaned_data['quantity']
    if qty < 1:
        raise forms.ValidationError("Quantity must be at least 1.")
    return qty

def product_bom(request):
    product = get_object_or_404(Product, name="Vendetta")
    capacity_data = get_build_capacity_data(product)
    recent_builds = ProductBuild.objects.filter(product=product).order_by('-built_at')[:5]

    preselect_order_id = request.GET.get("order")
    eligible_orders = Order.objects.filter(status="pending").select_related("customer")

    selected_order = None
    if preselect_order_id:
        selected_order = eligible_orders.filter(id=preselect_order_id).first()
        if not selected_order:
            messages.warning(request, "That order is not Pending, so it can't be started.")

    form = ProductBuildForm(
        order_queryset=eligible_orders,
        initial={"order": selected_order} if selected_order else None,
    )

    return render(request, "build/product_bom.html", {
        "product": product,
        "build_capacity": capacity_data,
        "recent_builds": recent_builds,
        "form": form,
        "selected_order": selected_order,
    })

@require_POST
@login_required
def build_product(request):
    product = get_object_or_404(Product, name="Vendetta")
    form = ProductBuildForm(request.POST)

    order_id = request.POST.get("order") or ""

    def bom_redirect():
        url = reverse("product_bom")
        return redirect(f"{url}?order={order_id}" if order_id else url)

    if not form.is_valid():
        messages.error(request, "Invalid build request.")
        return bom_redirect()

    quantity = form.cleaned_data["quantity"]
    order = form.cleaned_data["order"]

    product_components = (
        ProductComponent.objects
        .filter(product=product)
        .select_related("component")
    )

    if not product_components.exists():
        messages.error(request, "Cannot build product because no BOM components are defined.")
        return bom_redirect()

    invalid_bom_components = [
        pc.component.name
        for pc in product_components
        if pc.quantity_required is None or pc.quantity_required <= 0
    ]

    if invalid_bom_components:
        messages.error(
            request,
            "Cannot build product because some BOM quantities are missing or invalid: "
            f"{', '.join(invalid_bom_components)}."
        )
        return bom_redirect()

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
            if order:
                order = Order.objects.select_for_update().get(id=order.id)

                if order.status != "pending":
                    messages.error(request, "That order is no longer Pending, so it can't be started.")
                    return bom_redirect()

            build = ProductBuild.objects.create(
                product=product,
                order=order,
                quantity=quantity,
            )

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

def get_build_capacity_data(product):
    product_components = (
        ProductComponent.objects
        .filter(product=product)
        .select_related("component")
    )

    rows = []
    valid_rows = []
    warnings = []

    for pc in product_components:
        qty_required = pc.quantity_required
        stock_quantity = pc.component.stock_quantity

        row = {
            "product_component": pc,
            "component": pc.component,
            "quantity_required": qty_required,
            "stock_quantity": stock_quantity,
            "units_possible": None,
            "is_limiting": False,
            "issue": None,
            "counts_toward_capacity": False,
        }

        if qty_required is None:
            row["issue"] = "missing_qty"
        elif qty_required <= 0:
            row["issue"] = "invalid_qty"
        else:
            row["units_possible"] = int(stock_quantity / qty_required)
            row["counts_toward_capacity"] = True
            valid_rows.append(row)

        rows.append(row)

    if any(row["issue"] == "missing_qty" for row in rows):
        warnings.append("Some BOM lines are missing required quantity, so build capacity may be incomplete.")

    if any(row["issue"] == "invalid_qty" for row in rows):
        warnings.append("Some BOM lines have zero or invalid required quantity and were excluded from build capacity.")

    if not valid_rows:
        return {
            "buildable_units": None,
            "limiting_components": [],
            "has_incomplete_bom": bool(warnings),
            "warnings": warnings,
            "component_rows": rows,
        }

    buildable_units = min(row["units_possible"] for row in valid_rows)

    limiting_components = []
    for row in valid_rows:
        if row["units_possible"] == buildable_units:
            row["is_limiting"] = True
            limiting_components.append(row["component"])

    def row_sort_key(row):
        # Priority order:
        # 0 = out of stock
        # 1 = limiting
        # 2 = everything else
        if row["counts_toward_capacity"] and row["units_possible"] == 0:
            return 0
        elif row["is_limiting"]:
            return 1
        else:
            return 2
    
    rows.sort(key=row_sort_key)

    return {
        "buildable_units": buildable_units,
        "limiting_components": limiting_components,
        "has_incomplete_bom": bool(warnings),
        "warnings": warnings,
        "component_rows": rows,
    }

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
    selected_component = None

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
            component_id = request.POST.get("component")
            if component_id:
                selected_component = Component.objects.filter(pk=component_id).first()
    else:
        initial_component_id = request.GET.get("component")
        initial = {}
    
        if initial_component_id:
            selected_component = Component.objects.filter(pk=initial_component_id).first()
            if selected_component:
                initial["component"] = selected_component.id
    
        form = ReceiveStockForm(
            initial=initial,
            selected_component=bool(selected_component),
        )

    return render(
        request,
        "inventory/receive.html",
        {
            "form": form,
            "selected_component": selected_component,
        },
    )

@login_required
def inventory_adjust(request):
    selected_component = None

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
                selected_component = component
                form.add_error(None, e)
        else:
            component_id = request.POST.get("component")
            if component_id:
                selected_component = Component.objects.filter(pk=component_id).first()
    else:
        initial_component_id = request.GET.get("component")
        initial = {}

        if initial_component_id:
            selected_component = Component.objects.filter(pk=initial_component_id).first()
            if selected_component:
                initial["component"] = selected_component.id

        form = AdjustStockForm(
            initial=initial,
            selected_component=bool(selected_component),
        )

    return render(
        request,
        "inventory/adjust.html",
        {
            "form": form,
            "selected_component": selected_component,
        },
    )

@login_required
def component_ledger(request, id):
    component = get_object_or_404(Component, pk=id)

    reason_filter = request.GET.get("reason", "").strip()

    movements_qs = (
        StockMovement.objects
        .filter(component=component)
        .select_related("created_by")
        .order_by("-created_at", "-id")
    )

    if reason_filter:
        movements_qs = movements_qs.filter(reason=reason_filter)

    movements = list(movements_qs)

    # Running balance from current stock backwards
    running_balance = component.stock_quantity
    for movement in movements:
        movement.running_balance = running_balance
        running_balance -= movement.qty_delta

    reason_choices = StockMovement.Reason.choices

    return render(
        request,
        "inventory/ledger.html",
        {
            "component": component,
            "movements": movements,
            "reason_filter": reason_filter,
            "reason_choices": reason_choices,
        },
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

@login_required
def low_stock_dashboard(request):
    base_qs = (
        Component.objects
        .prefetch_related("suppliers")
    )

    low_stock_components = (
        with_bom_low_stock_threshold(base_qs)
        .filter(stock_quantity__lte=F("bom_low_stock_threshold"))
        .exclude(max_bom_quantity_required=0)
        .order_by("stock_quantity", "name")
    )

    return render(
        request,
        "inventory/low_stock_dashboard.html",
        {
            "components": low_stock_components,
            "low_stock_count": low_stock_components.count(),
        },
    )
