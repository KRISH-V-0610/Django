"""Root URLs: the Django admin plus the versioned Ninja API under /api/v1/."""

from django.contrib import admin
from django.urls import path

from ninja import NinjaAPI

from accounts.api import router as accounts_router
from orders.api import router as orders_router
from transactions.api import router as products_router


# Versioned API: everything lives under /api/v1/.
# A future breaking change would be a second NinjaAPI(version="2.0",
# urls_namespace="v2") mounted at /api/v2/, while v1 keeps working.
api_v1 = NinjaAPI(
    title="POC 1 API",
    version="1.0",
    urls_namespace="v1",
)
api_v1.add_router("/orders", orders_router)
api_v1.add_router("/auth", accounts_router)
api_v1.add_router("/products", products_router)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", api_v1.urls),
]
