"""
handler.py - the Lambda entry point.

Two routes now: POST /estimate (validate, pick a Strategy via the
Factory, compute, then PERSIST the result as a quote) and
GET /quotes/{quote_id} (look a previously-saved quote back up).

Still deliberately thin - all the real logic lives in
strategy.py/factory.py (unchanged from Week 1) and repository.py
(new this week). Notice this handler never imports boto3 at all.
"""

import json
from decimal import Decimal
from factory import ShippingStrategyFactory, UnsupportedSpeedTierError
from repository import QuoteRepository, InvalidQuoteIdError, QuoteNotFoundError

VALID_ZONES = {"local", "regional", "international"}


def _json_default(obj):
    """
    json.dumps has no native support for Decimal - and DynamoDB's
    read path returns EVERY numeric attribute as Decimal, even ones
    originally written as plain int or float. This is the read-side
    sibling of repository.py's _to_dynamodb_safe() (which handles the
    write side, float -> Decimal); this handles the opposite
    direction, Decimal -> whatever json.dumps can actually serialize.
    Whole numbers become int (so weight_kg reads back as 3, not 3.0);
    anything with a fractional part becomes float.
    """
    if isinstance(obj, Decimal):
        if obj % 1 == 0:
            return int(obj)
        return float(obj)
    raise TypeError("Object of type {} is not JSON serializable".format(type(obj).__name__))


def _response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=_json_default),
    }


def lambda_handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path_params = event.get("pathParameters") or {}
    query_params = event.get("queryStringParameters") or {}

    if method == "GET" and "quote_id" in path_params:
        return _get_quote(path_params["quote_id"], query_params)

    return _create_estimate(event)


def _create_estimate(event):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "Request body must be valid JSON"})

    weight_kg = body.get("weight_kg")
    destination_zone = body.get("destination_zone")
    speed_tier = body.get("speed_tier")

    # -- Input validation - the handler's job, not the Factory's or
    # the Repository's.
    if not isinstance(weight_kg, (int, float)) or isinstance(weight_kg, bool) or weight_kg <= 0:
        return _response(400, {"error": "weight_kg must be a positive number"})
    if destination_zone not in VALID_ZONES:
        return _response(400, {
            "error": "destination_zone must be one of: {}".format(", ".join(sorted(VALID_ZONES)))
        })

    # -- Strategy selection - the Factory's job.
    try:
        strategy = ShippingStrategyFactory.get_strategy(speed_tier)
    except UnsupportedSpeedTierError as e:
        return _response(400, {"error": str(e)})

    result = strategy.estimate(weight_kg, destination_zone)

    quote = {
        "speed_tier": speed_tier,
        "destination_zone": destination_zone,
        "weight_kg": weight_kg,
        "cost": result["cost"],
        "estimated_days": result["estimated_days"],
    }
    # -- Persistence - the Repository's job. The handler does not know
    # or care that this is DynamoDB.
    quote_id = QuoteRepository.save(quote)

    return _response(201, dict(quote, quote_id=quote_id))


def _get_quote(quote_id, query_params):
    # A real, caller-controlled instance of the CAP tradeoff: by
    # default, this reads eventually consistent data (cheaper, lower
    # latency, DynamoDB's own default). A caller who needs a
    # guarantee that they are seeing the most recent write can ask
    # for it explicitly - at a real cost, both in latency and in the
    # read capacity DynamoDB actually bills for. Neither choice is
    # "correct" - it depends what THIS caller actually needs.
    want_strong_consistency = (query_params or {}).get("consistent") == "true"

    try:
        if want_strong_consistency:
            quote = QuoteRepository.get_strongly_consistent(quote_id)
        else:
            quote = QuoteRepository.get(quote_id)
    except InvalidQuoteIdError as e:
        return _response(400, {"error": str(e)})
    except QuoteNotFoundError as e:
        return _response(404, {"error": str(e)})
    # Deliberately NOT catching RuntimeError (the 500 sentinel) or any
    # other unexpected exception here - that is the entire point of
    # that Break scenario: an unhandled failure should produce
    # Lambda's own generic error response, not one we shaped ourselves.

    return _response(200, quote)
