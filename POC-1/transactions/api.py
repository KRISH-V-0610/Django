from decimal import Decimal

from ninja import Field, Router, Schema

from accounts.auth import jwt_auth

from .models import Product

router = Router(auth=jwt_auth)


class ProductIn(Schema):
    name: str
    price: Decimal = Field(ge=0)
    stock: int = Field(ge=0)


class ProductOut(Schema):
    id: int
    name: str
    price: Decimal
    stock: int


@router.post("/", response={201: ProductOut})
def create_product(request, data: ProductIn):
    return 201, Product.objects.create(**data.dict())


@router.get("/", response=list[ProductOut])
def list_products(request):
    return Product.objects.order_by("id")
