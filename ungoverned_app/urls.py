# ungoverned_app/urls.py

from django.urls import path
from . import views

urlpatterns = [
    path('components/', views.component_list, name='component_list'),
    path("components/<int:id>/ledger/", views.component_ledger, name="component_ledger"),

    path('vendetta-bom/', views.product_bom, name='product_bom'),
    path('vendetta-bom/build/', views.build_product, name='build_product'),
    path('builds/<int:build_id>/cancel/', views.cancel_build, name='cancel_build'),

    path('orders/', views.orders_list, name='orders_list'),
    path("orders/<int:order_id>/start-build/", views.start_build, name="start_build"),
    path("orders/<int:order_id>/mark-complete/", views.mark_complete, name="mark_complete"),
    path("orders/<int:order_id>/ship/", views.ship_order, name="ship_order"),
    path("orders/<int:order_id>/cancel/", views.cancel_order, name="cancel_order"),
    path("orders/<int:order_id>/build/", views.build_product_for_order, name="build_product_for_order"),

    path("inventory/receive/", views.inventory_receive, name="inventory_receive"),
    path("inventory/adjust/", views.inventory_adjust, name="inventory_adjust"),
]
