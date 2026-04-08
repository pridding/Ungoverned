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
                on_hand = pc.component.stock_quantity
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

