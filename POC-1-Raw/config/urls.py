"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path

from ninja import NinjaAPI
# from orders.schemas import OrderSchema
from orders.api import router as orders_router



api = NinjaAPI()
api.add_router("/orders", orders_router)    

# @api.get("/hello")
# def hello(request):
#     return {"message": "Hello from Django Ninja!"}

# @api.post("/orders")
# def create_order(request, data: OrderSchema):
#     return {
#         "message": "Order received",
#         "product": data.product_name,
#         "amount": data.amount
#     }


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),
]
