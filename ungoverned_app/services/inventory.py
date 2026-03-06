# ungoverned_app/services/inventory.py
from dataclasses import dataclass
from typing import Optional, Any

from django.core.exceptions import ValidationError
from django.db import transaction

from ungoverned_app.models import Component, StockMovement

print(">>> HIT services.inventory.record_stock_movement <<<")

@dataclass
class RefInfo:
    reference_type: str = ""
    reference_id: Optional[int] = None


def _ref_to_info(ref: Any) -> RefInfo:
    if ref is None:
        return RefInfo(reference_type="Manual", reference_id=None)
    # If it's a model instance with pk
    pk = getattr(ref, "pk", None)
    name = ref.__class__.__name__
    return RefInfo(reference_type=name, reference_id=pk if isinstance(pk, int) else None)


def record_stock_movement(
    *,
    component_id: int,
    qty_delta: int,
    reason: str,
    user=None,
    note: str = "",
    ref=None,
) -> StockMovement:
    """
    Atomically:
      - locks the Component row (select_for_update)
      - prevents negative stock
      - updates Component.stock_quantity
      - inserts StockMovement
    """
    if qty_delta == 0:
        raise ValidationError("qty_delta cannot be 0.")

    ref_info = _ref_to_info(ref)

    with transaction.atomic():
        component = Component.objects.select_for_update().get(pk=component_id)

        new_qty = component.stock_quantity + qty_delta
        if new_qty < 0:
            raise ValidationError(
                f"Insufficient stock for {component}. "
                f"Current={component.stock_quantity}, Delta={qty_delta}, WouldBecome={new_qty}"
            )

        component.stock_quantity = new_qty
        component.save(update_fields=["stock_quantity"])

        movement = StockMovement.objects.create(
            component=component,
            qty_delta=qty_delta,
            reason=reason,
            note=note or "",
            reference_type=ref_info.reference_type or "",
            reference_id=ref_info.reference_id,
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )

        return movement
