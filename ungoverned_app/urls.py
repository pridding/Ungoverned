# ungoverned_app/urls.py

from django.contrib import admin
from django.urls import path
from ungoverned_app import views

urlpatterns = [

    path('components/', views.component_list, name='component_list'),
    path("components/low-stock/", views.low_stock_dashboard, name="low_stock_dashboard"),
    path("components/<int:id>/", views.component_detail, name="component_detail"),
    path("components/<int:id>/ledger/", views.component_ledger, name="component_ledger"),

    path("inventory/receive/", views.inventory_receive, name="inventory_receive"),
    path("inventory/adjust/", views.inventory_adjust, name="inventory_adjust"),

    path('build/', views.product_bom, name='product_bom'),
    path('build/submit/', views.build_product, name='build_product'),
    path('builds/<int:build_id>/cancel/', views.cancel_build, name='cancel_build'),

    path("customers/<int:id>/edit/", views.customer_edit, name="customer_edit"),
    path("customers/<int:id>/", views.customer_detail, name="customer_detail"),
    path("customers/", views.customers_list, name="customers_list"),
    path("customers/new/", views.customer_create, name="customer_create"),

    path('orders/', views.orders_list, name='orders_list'),
    path("orders/<int:order_id>/start-build/", views.start_build, name="start_build"),
    path("orders/<int:order_id>/mark-complete/", views.mark_complete, name="mark_complete"),
    path("orders/<int:order_id>/ship/", views.ship_order, name="ship_order"),
    path("orders/<int:order_id>/cancel/", views.cancel_order, name="cancel_order"),
    path("orders/<int:order_id>/", views.order_detail, name="order_detail"),
    path("orders/<int:order_id>/reopen/", views.reopen_order, name="reopen_order"),

]
