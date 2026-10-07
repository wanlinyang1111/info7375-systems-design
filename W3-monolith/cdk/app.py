#!/usr/bin/env python3
import aws_cdk as cdk
from stack import MonolithStack

app = cdk.App()

MonolithStack(
    app,
    "Week3MonolithStack",
    env=cdk.Environment(
        account=app.node.try_get_context("account"),
        region=app.node.try_get_context("region") or "us-east-1",
    ),
    description="Info7375 Week 3 - Monolith Architecture Lab",
)

app.synth()
