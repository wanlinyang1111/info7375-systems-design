"""
Orders route - 20% of load test traffic.

Exercises both Strategy seams:
  - PaymentProcessor.charge()   -> PaymentStrategy
  - ShippingCalculator.quote()  -> ShippingStrategy

Both are injected via FastAPI dependency injection.
The route never imports StripeStrategy or FlatRateStrategy directly.
"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from repository.database import get_db
from models.orm import Order, OrderItem, Product, User
from services.payment.strategies import PaymentProcessor, get_payment_processor
from services.shipping.strategies import ShippingCalculator, get_shipping_calculator
from services.cache import invalidate

log = logging.getLogger(__name__)
router = APIRouter()


# -- Request / Response models ----------------------------------------------
class OrderItemRequest(BaseModel):
    product_id: str
    quantity:   int = Field(ge=1)


class CreateOrderRequest(BaseModel):
    user_id:         str
    items:           list[OrderItemRequest] = Field(min_length=1)
    card_token:      str = Field(default="tok_test")
    shipping_method: str = Field(default="flat_rate")


class OrderItemResponse(BaseModel):
    product_id: str
    quantity:   int
    unit_price: float

    class Config:
        from_attributes = True


class OrderResponse(BaseModel):
    id:               str
    user_id:          str
    status:           str
    total_amount:     float
    shipping_cost:    float
    payment_provider: str | None
    shipping_method:  str | None

    class Config:
        from_attributes = True


# -- Create order -----------------------------------------------------------
@router.post("/", response_model=OrderResponse, status_code=201)
def create_order(
    req: CreateOrderRequest,
    db: Session = Depends(get_db),
    payment: PaymentProcessor = Depends(get_payment_processor),
):
    # Verify user exists
    user = db.query(User).filter(User.id == req.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Fetch products and calculate totals
    total_weight = 0.0
    subtotal     = 0.0
    line_items   = []

    for item_req in req.items:
        product = db.query(Product).filter(Product.id == item_req.product_id).first()
        if not product:
            raise HTTPException(
                status_code=404, detail=f"Product {item_req.product_id} not found"
            )
        if product.stock_count < item_req.quantity:
            raise HTTPException(
                status_code=409,
                detail=f"Insufficient stock for {product.name}: "
                       f"have {product.stock_count}, want {item_req.quantity}",
            )
        subtotal     += product.price * item_req.quantity
        total_weight += product.weight_kg * item_req.quantity
        line_items.append((product, item_req.quantity))

    # Shipping quote - Strategy pattern in action
    shipping_calc = get_shipping_calculator(req.shipping_method)
    quote         = shipping_calc.quote(total_weight, len(line_items))

    # Create Order record
    order = Order(
        user_id=req.user_id,
        status="pending",
        total_amount=round(subtotal + quote.cost, 2),
        shipping_cost=quote.cost,
        shipping_method=quote.method,
    )
    db.add(order)
    db.flush()  # get order.id before payment

    # Process payment - Strategy pattern in action
    payment_result = payment.charge(
        amount=order.total_amount,
        card_token=req.card_token,
        order_id=order.id,
    )

    if not payment_result.success:
        db.rollback()
        log.warning("payment_failed", extra={"order_id": order.id,
                                              "error": payment_result.error})
        raise HTTPException(status_code=402, detail="Payment failed")

    # Update order status and save items
    order.status           = "confirmed"
    order.payment_provider = payment_result.provider

    for product, qty in line_items:
        db.add(OrderItem(
            order_id=order.id,
            product_id=product.id,
            quantity=qty,
            unit_price=product.price,
        ))
        product.stock_count -= qty
        invalidate(f"product:{product.id}")   # evict stale cached stock count

    db.commit()
    db.refresh(order)

    log.info("order_created", extra={"order_id": order.id,
                                      "total": order.total_amount,
                                      "payment_provider": order.payment_provider,
                                      "shipping_method": order.shipping_method})
    return OrderResponse.from_orm(order)


@router.get("/{order_id}", response_model=OrderResponse)
def get_order(order_id: str, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return OrderResponse.from_orm(order)
