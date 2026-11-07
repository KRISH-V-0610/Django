
# External Packages
from ninja import Header, Router, Query
from django.db import transaction
from django.shortcuts import get_object_or_404

# Internal Packages
from accounts.auth import jwt_auth
from accounts.schemas import ErrorSchema
from idempotency import service as idempotency
from idempotency.service import make_fingerprint
from transactions.services import OrderError, place_order
from .models import Order
from .schemas import OrderOut, OrderSchema, OrderUpdateSchema, PlaceOrderOut, PlaceOrderSchema

router = Router(auth=jwt_auth)

@router.post("/", response={201: OrderOut})
def create_order(request, data: OrderSchema):
    order = Order.objects.create(
        product_name=data.product_name,
        amount=data.amount
    )

    return 201, order


@router.post(
    "/place",
    response={201: PlaceOrderOut, 400: ErrorSchema, 404: ErrorSchema,
              409: ErrorSchema, 422: ErrorSchema, 500: ErrorSchema},
)   
def place_order_api(
    request,
    data: PlaceOrderSchema,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        return 400, {"detail": "Idempotency-Key header is required."}

    # 1. claim the key (or get a ready-made reply for a repeated request)
    fingerprint = make_fingerprint(request, data.dict())
    record, reply = idempotency.begin(request.auth, "place_order", idempotency_key, fingerprint)
    if reply:
        return reply

    # 2. run the business operation + save its result in ONE transaction
    try:
        with transaction.atomic():
            body = place_order(data.product_id, data.quantity, data.fail_at)
            idempotency.complete(record, 201, body)
    except OrderError as exc:
        idempotency.abandon(record)  # rolled back -> let the client retry
        return exc.status_code, {"detail": exc.message}
    except Exception:
        idempotency.abandon(record)
        raise

    return 201, body


@router.get("/", response=list[OrderOut])
def list_orders(
    request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    return Order.objects.order_by(
        "-created_at", "-id"
    )[offset:offset + limit]

@router.get("/{order_id}", response=OrderOut)
def get_order(request, order_id: int):
    # get_object_or_404 returns the row, or answers 404 if it does not exist
    return get_object_or_404(Order, id=order_id)

@router.patch("/{order_id}", response={200: OrderOut, 400: ErrorSchema})
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

    return 200, order


@router.delete("/{order_id}", response={204: None})
def delete_order(request, order_id: int):
    order = get_object_or_404(Order, id=order_id)
    order.delete()

    return 204, None
