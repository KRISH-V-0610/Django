from django.urls import path
from ninja import NinjaAPI

from orders.api import router as orders_router

api = NinjaAPI(title="POC 4 API (Temporal order processing)", version="1.0")
api.add_router("/", orders_router)

urlpatterns = [
    path("api/", api.urls),
]
