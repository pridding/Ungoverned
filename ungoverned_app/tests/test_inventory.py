# ungoverned_app/tests/test_inventory.py
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from ungoverned_app.models import Component, StockMovement
from ungoverned_app.services.inventory import record_stock_movement

User = get_user_model()

class InventoryLedgerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tester", password="pass")
        self.component = Component.objects.create(
            # adapt fields to your actual Component model requirements
            name="Widget Screw",
            stock_quantity=10,
            low_stock_threshold=2,
        )

    def test_receive_creates_movement_and_updates_stock(self):
        m = record_stock_movement(
            component_id=self.component.id,
            qty_delta=5,
            reason=StockMovement.Reason.RECEIVE,
            user=self.user,
            note="Initial delivery",
        )
        self.component.refresh_from_db()
        self.assertEqual(self.component.stock_quantity, 15)
        self.assertEqual(m.qty_delta, 5)

    def test_prevent_negative_stock(self):
        with self.assertRaises(ValidationError):
            record_stock_movement(
                component_id=self.component.id,
                qty_delta=-999,
                reason=StockMovement.Reason.ADJUSTMENT,
                user=self.user,
                note="Bad adjustment",
            )
        self.component.refresh_from_db()
        self.assertEqual(self.component.stock_quantity, 10)
        self.assertEqual(StockMovement.objects.count(), 0)
