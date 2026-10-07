#!/usr/bin/env python3
"""
compare_read_consistency.py

Measures the ACTUAL latency and read-capacity difference between
eventually consistent and strongly consistent DynamoDB reads.

IMPORTANT: this MUST be run against your real, deployed table - not
against moto. Confirmed directly while building this: moto reports
IDENTICAL ConsumedCapacity (0.5) for both read modes. Real DynamoDB
reports 0.5 for eventually consistent and 1.0 for strongly consistent
- a genuine 2x difference. moto has no reason to faithfully model
DynamoDB's internal billing/capacity accounting, so it does not - this
is the same "know what your mock does and does not reproduce"
discipline this course has applied to every mock used so far.

Usage:
    export QUOTES_TABLE=<the real table name from your stack outputs>
    python3 compare_read_consistency.py <a-real-existing-quote-id>
"""
import os
import sys
import time
import statistics

import boto3

QUOTES_TABLE = os.environ.get("QUOTES_TABLE")
ITERATIONS = 50


def _time_reads(table, quote_id, consistent_read):
    durations = []
    capacity_units = None
    for _ in range(ITERATIONS):
        start = time.perf_counter()
        response = table.get_item(
            Key={"quote_id": quote_id},
            ConsistentRead=consistent_read,
            ReturnConsumedCapacity="TOTAL",
        )
        durations.append(time.perf_counter() - start)
        capacity_units = response.get("ConsumedCapacity", {}).get("CapacityUnits")
    return durations, capacity_units


def main():
    if not QUOTES_TABLE:
        print("Set QUOTES_TABLE to your real deployed table name first.")
        sys.exit(1)
    if len(sys.argv) < 2:
        print("Usage: python3 compare_read_consistency.py <existing-quote-id>")
        sys.exit(1)

    quote_id = sys.argv[1]
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(QUOTES_TABLE)

    print("Confirming quote_id exists first...")
    if "Item" not in table.get_item(Key={"quote_id": quote_id}):
        print("No quote found with that id - create one first via POST /estimate.")
        sys.exit(1)

    print("Running {} eventually consistent reads...".format(ITERATIONS))
    eventual_durations, eventual_capacity = _time_reads(table, quote_id, consistent_read=False)

    print("Running {} strongly consistent reads...".format(ITERATIONS))
    strong_durations, strong_capacity = _time_reads(table, quote_id, consistent_read=True)

    print()
    print("=== Latency (milliseconds) ===")
    print("  Eventually consistent - mean: {:.3f}ms  median: {:.3f}ms".format(
        statistics.mean(eventual_durations) * 1000, statistics.median(eventual_durations) * 1000
    ))
    print("  Strongly consistent   - mean: {:.3f}ms  median: {:.3f}ms".format(
        statistics.mean(strong_durations) * 1000, statistics.median(strong_durations) * 1000
    ))
    print()
    print("=== Read Capacity Units consumed per call ===")
    print("  Eventually consistent:", eventual_capacity)
    print("  Strongly consistent:  ", strong_capacity)
    print()
    print("If strong consistency's CapacityUnits is exactly double the")
    print("eventually consistent value, that is not a coincidence - it is")
    print("documented AWS pricing behavior: a strongly consistent read of")
    print("an item costs twice what an eventually consistent read of the")
    print("same item costs, every time, by design.")


if __name__ == "__main__":
    main()
