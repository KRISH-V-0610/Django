from datetime import datetime
from decimal import Decimal

from typing import Literal

from ninja import Field, Schema


class OrderOut(Schema):
    id: int
    product_name: str
    amount: Decimal
    created_at: datetime
    status: str


class PlaceOrderSchema(Schema):
    product_id: int
    quantity: int = Field(1, ge=1)
    # only for demos/tests: pretend something crashes at this step
    fail_at: Literal["after_stock", "after_record", "free_sample"] | None = None


class PlaceOrderOut(Schema):
    order_id: int
    status: str
    product_id: int
    quantity: int
    amount: Decimal
    stock_left: int
    free_sample: bool


class OrderSchema(Schema):
    product_name: str
    amount: Decimal

class OrderUpdateSchema(Schema):
    product_name: str | None = None
    amount: Decimal | None = None
    
    
    