from ninja import Schema
from decimal import Decimal

class OrderSchema(Schema):
    product_name: str
    amount: Decimal

class OrderUpdateSchema(Schema):
    product_name: str | None = None
    amount: Decimal | None = None
    
    
    