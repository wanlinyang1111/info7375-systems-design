"""
handler.py - the Lambda entry point.

Deliberately thin: parse and validate the request, ask the Factory for
a Strategy, call it, shape the response. All the actual business logic
lives in strategy.py/factory.py - which is exactly why those two files
can be tested with zero AWS involvement at all (see
test_shipping_estimator.py). The handler's only job is translating
between "API Gateway event shape" and "plain Python function call."
"""

import json
from factory import ShippingStrategyFactory, UnsupportedSpeedTierError

VALID_ZONES = {"local", "regional", "international"}


def _response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "Request body must be valid JSON"})

    weight_kg = body.get("weight_kg")
    destination_zone = body.get("destination_zone")
    speed_tier = body.get("speed_tier")

    # -- Input validation - the handler's job, not the Factory's or the
    # Strategy's. A missing/negative weight or an unrecognized zone is
    # just bad data, not "we don't know how to handle this speed tier".
    # 400 errors are CALLER errors. 500 errors are SERVER errors
    if not isinstance(weight_kg, (int, float)) or isinstance(weight_kg, bool) or weight_kg <= 0:
        return _response(400, {"error": "weight_kg must be a positive number"})
    if destination_zone not in VALID_ZONES:
        return _response(400, {
            "error": "destination_zone must be one of: {}".format(", ".join(sorted(VALID_ZONES)))
        })

    # -- Strategy selection - THIS is the Factory's job. An unsupported
    # speed_tier is a different KIND of failure than the checks above:
    # the request is well-formed, we simply have no Strategy for it.
    try:
        strategy = ShippingStrategyFactory.get_strategy(speed_tier)
    except UnsupportedSpeedTierError as e:
        return _response(400, {"error": str(e)})

    result = strategy.estimate(weight_kg, destination_zone)
    return _response(200, {
        "speed_tier": speed_tier,
        "destination_zone": destination_zone,
        "weight_kg": weight_kg,
        "cost": result["cost"],
        "estimated_days": result["estimated_days"],
    })
