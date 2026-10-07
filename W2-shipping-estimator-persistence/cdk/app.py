#!/usr/bin/env python3
import os
import aws_cdk as cdk
from stack import ShippingEstimatorStack

app = cdk.App()

ShippingEstimatorStack(
    app,
    "Week2ShippingEstimatorStack",
    env=cdk.Environment(
        account=app.node.try_get_context("account") or os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=app.node.try_get_context("region") or os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    ),
    description="Info7375 Week 2 - Shipping Cost Estimator + Quotes (Repository + Singleton patterns)",
)

app.synth()
