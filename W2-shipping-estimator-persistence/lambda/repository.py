"""
repository.py - the Repository pattern.

Sits between the handler and DynamoDB so that nothing else in this
codebase - not the handler, not strategy.py/factory.py - ever touches
boto3 directly. Two operations: save a quote, get a quote back by ID.

The DynamoDB table object below is constructed once, at module level,
not inside a function - this is also, incidentally, a Singleton: one
shared instance, reused across every warm Lambda invocation, rather
than reconnecting on every single request. This is standard Lambda
performance practice AND a classic GoF pattern showing up because the
platform rewards it, not because anyone consciously "applied" it.
"""

import os
import re
import uuid
from decimal import Decimal

import boto3

QUOTES_TABLE = os.environ.get("QUOTES_TABLE", "quotes")

# Singleton - constructed once per execution environment, reused
# across every warm invocation.
_dynamodb = boto3.resource("dynamodb")
_table = _dynamodb.Table(QUOTES_TABLE)

# A quote_id must look like a real UUID4 - anything else is rejected
# BEFORE ever reaching DynamoDB. This is what makes the 400
# ("malformed ID") and 404 ("well-formed, but does not exist") Break
# scenarios genuinely different code paths, not just different
# messages for the same failure.
_QUOTE_ID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class InvalidQuoteIdError(ValueError):
    """
    Raised when a quote_id is not even a legal ID format. A
    DIFFERENT failure than QuoteNotFoundError below - "the request is
    garbage" is not the same problem as "the request is well-formed
    but nothing matches it."
    """
    pass


class QuoteNotFoundError(LookupError):
    """Raised when a quote_id is well-formed but no matching quote exists."""
    pass


def _to_dynamodb_safe(obj):
    """
    Recursively convert Python float to Decimal - DynamoDB's boto3
    resource layer rejects raw float outright. This is the exact bug
    that hit Week 5's order_producer on its first real order. A
    "quote" has a "cost" field, the same shape as that bug - fixed
    here from the start rather than rediscovered the same way twice.
    """
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, list):
        return [_to_dynamodb_safe(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _to_dynamodb_safe(v) for k, v in obj.items()}
    return obj


class QuoteRepository:
    @staticmethod
    def save(quote):
        quote_id = str(uuid.uuid4())
        item = _to_dynamodb_safe(dict(quote, quote_id=quote_id))
        _table.put_item(Item=item)
        return quote_id

    @staticmethod
    def _validate_quote_id(quote_id):
        """
        Shared by get() and get_strongly_consistent() below - both
        need the same sentinel/format checks before ever touching
        DynamoDB. Kept as one function so the two read paths cannot
        drift out of sync with each other.

        The sentinel check runs FIRST, before format validation,
        because "__trigger_500__" is not a valid UUID and would
        otherwise just be caught as a 400 (malformed ID) instead of
        triggering the deliberate, uncaught 500 it exists to produce.
        """
        if quote_id == "__trigger_500__":
            raise RuntimeError("Simulated unhandled failure in QuoteRepository.get()")
        if not _QUOTE_ID_PATTERN.match(quote_id):
            raise InvalidQuoteIdError(
                "quote_id '{}' is not a valid quote ID format".format(quote_id)
            )

    @staticmethod
    def get(quote_id):
        """
        The default read path - EVENTUALLY CONSISTENT (DynamoDB's own
        default; ConsistentRead is not specified here at all).

        This is a real, present-tense instance of the same tradeoff
        CAP theorem describes: DynamoDB replicates every item across
        multiple storage nodes for durability, and an eventually
        consistent read may be served by a replica that has not yet
        received the very latest write. In exchange for that small,
        usually sub-millisecond risk, this read is cheaper (half the
        read capacity of a strongly consistent read - see
        get_strongly_consistent() below) and has lower latency,
        because it does not need to check in with the most
        up-to-date copy of the data before answering. This is
        choosing Availability over strict Consistency, not as an
        abstract triangle diagram, but as the literal default
        behavior of the one get_item() call this whole app makes.
        """
        QuoteRepository._validate_quote_id(quote_id)
        response = _table.get_item(Key={"quote_id": quote_id})
        if "Item" not in response:
            raise QuoteNotFoundError("No quote found with id '{}'".format(quote_id))
        return response["Item"]

    @staticmethod
    def get_strongly_consistent(quote_id):
        """
        The alternative read path - STRONGLY CONSISTENT
        (ConsistentRead=True). Guarantees the response reflects the
        most recent successful write, at the cost of TWICE the read
        capacity of get() above for the same item, and slightly
        higher latency (DynamoDB must check with a copy of the data
        known to be current before it can answer).

        moto (used in this project's local tests) does NOT faithfully
        model this cost difference - it reports identical
        ConsumedCapacity for both calls, because RCU accounting is
        real AWS billing/capacity internals a mock library has no
        reason to replicate. The actual difference is only visible
        against the real, deployed table - see
        compare_read_consistency.py.
        """
        QuoteRepository._validate_quote_id(quote_id)
        response = _table.get_item(Key={"quote_id": quote_id}, ConsistentRead=True)
        if "Item" not in response:
            raise QuoteNotFoundError("No quote found with id '{}'".format(quote_id))
        return response["Item"]
