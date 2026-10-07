#!/usr/bin/env python3
import aws_cdk as cdk
from stack import MicroservicesStack

app = cdk.App()

MicroservicesStack(
    app,
    "Week4MicroservicesStack",
    env=cdk.Environment(
        account=app.node.try_get_context("account"),
        region=app.node.try_get_context("region") or "us-east-1",
    ),
    description="INFO7375 Week 4 — Microservices Architecture Lab",
)

app.synth()
