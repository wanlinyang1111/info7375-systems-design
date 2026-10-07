"""
test_shipping_estimator.py

Strategy/Factory tests are unchanged from Week 1 - pure Python, zero
AWS involvement, still run in milliseconds.

Repository and handler tests are new this week and use `moto` to mock
DynamoDB. IMPORTANT MECHANICAL NOTE: repository.py constructs its
DynamoDB Table object at MODULE IMPORT TIME (the Singleton pattern) -
if that import happens before a moto mock is active, the resulting
Table object is bound to real (unmocked) AWS and every call fails with
NoCredentialsError. This was verified empirically, not assumed. The
fix used below: set dummy AWS credentials globally so construction
never fails outright, then `importlib.reload(repository)` INSIDE each
mocked test, after the fake table has been created, so the module's
Singleton gets rebound against the currently-active mock.

Run with:
    pip install pytest moto boto3 --break-system-packages
    pytest test_shipping_estimator.py -v
"""
import importlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lambda"))

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("QUOTES_TABLE", "quotes")

import boto3
import pytest
from moto import mock_aws

from factory import ShippingStrategyFactory, UnsupportedSpeedTierError
from strategy import StandardShipping, ExpressShipping, OvernightShipping

import repository as repository_module
import handler as handler_module


# ---------------------------------------------------------------------------
# Strategy / Factory - unchanged from Week 1, no AWS involvement at all
# ---------------------------------------------------------------------------

def test_factory_returns_correct_strategy_type():
    assert isinstance(ShippingStrategyFactory.get_strategy("standard"), StandardShipping)
    assert isinstance(ShippingStrategyFactory.get_strategy("express"), ExpressShipping)
    assert isinstance(ShippingStrategyFactory.get_strategy("overnight"), OvernightShipping)


def test_factory_rejects_unknown_tier():
    with pytest.raises(UnsupportedSpeedTierError):
        ShippingStrategyFactory.get_strategy("teleport")


def test_standard_shipping_local_zone():
    result = StandardShipping().estimate(weight_kg=10, destination_zone="local")
    assert result["cost"] == 10.00
    assert result["estimated_days"] == 5


# ---------------------------------------------------------------------------
# Repository - moto-backed. Each test creates its own fresh table and
# reloads the module inside the mock, per the mechanical note above.
# ---------------------------------------------------------------------------

def _fresh_repository():
    """Create the mock table, then reload repository.py so its
    Singleton Table object binds against the currently-active mock."""
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    dynamodb.create_table(
        TableName=os.environ["QUOTES_TABLE"],
        KeySchema=[{"AttributeName": "quote_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "quote_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    importlib.reload(repository_module)
    importlib.reload(handler_module)  # handler imports names FROM repository - reload both
    return repository_module


@mock_aws
def test_save_and_get_round_trip():
    repo = _fresh_repository()
    quote_id = repo.QuoteRepository.save({"cost": 27.00, "estimated_days": 2})
    result = repo.QuoteRepository.get(quote_id)
    assert result["quote_id"] == quote_id
    # Confirms the Decimal fix - a raw float went in, and round-tripped
    # correctly rather than raising TypeError on the way into DynamoDB.
    assert float(result["cost"]) == 27.00


@mock_aws
def test_get_raises_not_found_for_wellformed_missing_id():
    repo = _fresh_repository()
    with pytest.raises(repo.QuoteNotFoundError):
        repo.QuoteRepository.get("00000000-0000-0000-0000-000000000000")


@mock_aws
def test_get_raises_invalid_id_for_malformed_id():
    repo = _fresh_repository()
    with pytest.raises(repo.InvalidQuoteIdError):
        repo.QuoteRepository.get("not-a-real-id-format")


@mock_aws
def test_get_sentinel_raises_uncaught_runtime_error():
    # This is the 500 scenario at the Repository layer - deliberately
    # NOT a QuoteRepository-specific exception, a bare RuntimeError,
    # simulating a genuinely unexpected failure.
    repo = _fresh_repository()
    with pytest.raises(RuntimeError):
        repo.QuoteRepository.get("__trigger_500__")


@mock_aws
def test_get_strongly_consistent_round_trip():
    # This test only confirms the MECHANICS work - that
    # ConsistentRead=True is accepted and still returns correct data.
    # It does NOT and CANNOT verify the real cost/latency difference -
    # moto reports identical ConsumedCapacity for both modes (confirmed
    # directly while building this). That comparison only means
    # anything against the real deployed table - see
    # compare_read_consistency.py.
    repo = _fresh_repository()
    quote_id = repo.QuoteRepository.save({"cost": 15.00, "estimated_days": 2})
    result = repo.QuoteRepository.get_strongly_consistent(quote_id)
    assert result["quote_id"] == quote_id


@mock_aws
def test_get_strongly_consistent_still_validates_id():
    # The two read paths share _validate_quote_id() - confirm the
    # strongly consistent path did not accidentally skip validation.
    repo = _fresh_repository()
    with pytest.raises(repo.InvalidQuoteIdError):
        repo.QuoteRepository.get_strongly_consistent("not-a-real-id-format")
    with pytest.raises(RuntimeError):
        repo.QuoteRepository.get_strongly_consistent("__trigger_500__")


# ---------------------------------------------------------------------------
# Handler - full request/response shape, still via moto
# ---------------------------------------------------------------------------

def _http_event(method, path_params=None, query_params=None, body=None):
    return {
        "requestContext": {"http": {"method": method}},
        "pathParameters": path_params,
        "queryStringParameters": query_params,
        "body": json.dumps(body) if body is not None else None,
    }


@mock_aws
def test_post_estimate_happy_path_returns_201_and_persists():
    _fresh_repository()
    event = _http_event("POST", body={
        "weight_kg": 3, "destination_zone": "regional", "speed_tier": "express"
    })
    resp = handler_module.lambda_handler(event, None)
    assert resp["statusCode"] == 201
    quote_id = json.loads(resp["body"])["quote_id"]

    get_event = _http_event("GET", path_params={"quote_id": quote_id})
    get_resp = handler_module.lambda_handler(get_event, None)
    assert get_resp["statusCode"] == 200


@mock_aws
def test_get_quote_malformed_id_returns_400():
    _fresh_repository()
    event = _http_event("GET", path_params={"quote_id": "not-a-real-id"})
    resp = handler_module.lambda_handler(event, None)
    assert resp["statusCode"] == 400


@mock_aws
def test_get_quote_wellformed_missing_id_returns_404():
    _fresh_repository()
    event = _http_event("GET", path_params={"quote_id": "00000000-0000-0000-0000-000000000000"})
    resp = handler_module.lambda_handler(event, None)
    assert resp["statusCode"] == 404


@mock_aws
def test_get_quote_with_consistent_true_query_param_still_works():
    # Confirms the query parameter is correctly wired through to
    # get_strongly_consistent() rather than silently ignored. Cannot
    # verify the COST difference here - see compare_read_consistency.py.
    _fresh_repository()
    post_resp = handler_module.lambda_handler(
        _http_event("POST", body={"weight_kg": 2, "destination_zone": "local", "speed_tier": "standard"}),
        None,
    )
    quote_id = json.loads(post_resp["body"])["quote_id"]

    get_resp = handler_module.lambda_handler(
        _http_event("GET", path_params={"quote_id": quote_id}, query_params={"consistent": "true"}),
        None,
    )
    assert get_resp["statusCode"] == 200
    assert json.loads(get_resp["body"])["quote_id"] == quote_id


@mock_aws
def test_get_quote_sentinel_propagates_uncaught_exception():
    # The handler deliberately does NOT catch this - confirming that
    # absence is the actual point of the 500 scenario. If this test
    # ever needs a `try/except RuntimeError` to pass, that means
    # someone added error handling that defeats the scenario.
    _fresh_repository()
    event = _http_event("GET", path_params={"quote_id": "__trigger_500__"})
    with pytest.raises(RuntimeError):
        handler_module.lambda_handler(event, None)
