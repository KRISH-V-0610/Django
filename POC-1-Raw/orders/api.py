

# External Packages
from ninja import Router, Query
from django.shortcuts import get_object_or_404

# Internal Packages
from .models import Order
from .schemas import OrderSchema, OrderUpdateSchema

router = Router()

@router.post("/")
def create_order(request, data: OrderSchema):
    order = Order.objects.create(
        product_name=data.product_name,
        amount=data.amount
    )
    
    
    return {
        "id": order.id,
        "product_name": order.product_name,
        "amount": str(order.amount)
    }
    
    
@router.get("/")
def list_orders(
    request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    orders = Order.objects.order_by(
        "-created_at", "-id"
    )[offset:offset + limit]

    return [
        {
            "id": order.id,
            "product_name": order.product_name,
            "amount": str(order.amount),
            "created_at": order.created_at
        }
        for order in orders
    ]

@router.get("/{order_id}")
def get_order(request, order_id: int):
  
  
    # WITH SHORTCUT -------------------
    order = get_object_or_404(Order, id=order_id)
    

    # WITHOUT SHORTCUT ---------------- 
    # from django.http import Http404

    # try:
    #   order = Order.objects.get(id=order_id)
    # except Order.DoesNotExist:
    #   raise Http404("Order not found")

    return {
        "id": order.id,
        "product_name": order.product_name,
        "amount": str(order.amount),
        "created_at": order.created_at
    }   

@router.patch("/{order_id}")
def update_order(request, order_id: int, data: OrderUpdateSchema):
    order = get_object_or_404(Order, id=order_id)

    updates = data.dict(exclude_unset=True)

    if not updates:
        return 400, {"detail": "At least one field must be provided."}

    if any(value is None for value in updates.values()):
        return 400, {"detail": "Fields cannot be null."}

    for field, value in updates.items():
        setattr(order, field, value)

    order.save(update_fields=list(updates.keys()))

    return {
        "id": order.id,
        "product_name": order.product_name,
        "amount": str(order.amount),
        "created_at": order.created_at
    }