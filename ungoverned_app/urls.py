# ungoverned_app/urls.py

from django.urls import path
from . import views

urlpatterns = [
    path('vendetta-bom/', views.product_bom, name='product_bom'),
    path('vendetta-bom/build/', views.build_product, name='build_product'),
    path('components/', views.component_list, name='component_list'),
    path('builds/<int:build_id>/cancel/', views.cancel_build, name='cancel_build'),
    path('orders/', views.order_list, name='order_list'),
]
