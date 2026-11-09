from decimal import Decimal
from uuid import UUID

from django.shortcuts import get_object_or_404
from ninja import Field, Query, Router, Schema

from . import services
from .filters import search_products
from .models import Customer, Product

router = Router()


class ErrorOut(Schema):
    detail: str

class CustomerIn(Schema):
    name: str
    email: str


class CustomerOut(Schema):
    id: int
    name: str
    email: str


@router.post("/customers", response={201: CustomerOut, 409: ErrorOut})
def create_customer(request, data: CustomerIn):
    
    customer = Customer(name=data.name, email=data.email)
    error = services.try_save(customer)  # the DATABASE decides if the email is a duplicate
    if error:
        return 409, {"detail": "A customer with this email already exists."}
    return 201, customer


# ----------------------------------------------------------------- products
class ProductIn(Schema):
    sku: str
    name: str
    price: Decimal
    stock: int = 0
    is_active: bool = True


class ProductOut(Schema):
    id: UUID
    sku: str
    name: str
    price: Decimal
    stock: int
    views: int
    is_active: bool


@router.post("/products", response={201: ProductOut, 400: ErrorOut})
def create_product(request, data: ProductIn):
    
    product = Product(**data.dict())
    error = services.try_save(product)
    if error:
        return 400, {"detail": error}
    return 201, product


class ProductFilter(Schema):
    search: str | None = None
    names: list[str] | None = None
    exclude: str | None = None
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    in_stock: bool = False
    only_active: bool = True


@router.get("/products/search", response=list[ProductOut])
def search(request, filters: ProductFilter = Query(...)):
    return search_products(**filters.dict())


@router.get("/products/{product_id}", response=ProductOut)
def get_product(request, product_id: UUID):
    services.add_view(product_id)  # counter bumped with F()
    return get_object_or_404(Product, pk=product_id)


# -------------------------------------------------------------------- stock
class AmountIn(Schema):
    amount: int = Field(..., gt=0)


@router.post("/products/{product_id}/add-stock", response=ProductOut)
def add_stock(request, product_id: UUID, data: AmountIn):
    get_object_or_404(Product, pk=product_id)
    services.add_stock(product_id, data.amount)
    return Product.objects.get(pk=product_id)


@router.post("/products/{product_id}/remove-stock", response={200: ProductOut, 400: ErrorOut})
def remove_stock(request, product_id: UUID, data: AmountIn):
    get_object_or_404(Product, pk=product_id)
    if not services.remove_stock(product_id, data.amount):
        return 400, {"detail": "Not enough stock."}
    return 200, Product.objects.get(pk=product_id)
