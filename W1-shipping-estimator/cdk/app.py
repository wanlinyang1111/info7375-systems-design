#!/usr/bin/env python3
import os
import aws_cdk as cdk
from stack import ShippingEstimatorStack

app = cdk.App()

ShippingEstimatorStack(
    app,
    "Week1ShippingEstimatorStack",
    # Auto-discovered, not hardcoded - this artifact is distributed to
    # students, each deploying to their own AWS account.
    # CDK_DEFAULT_ACCOUNT/CDK_DEFAULT_REGION are set automatically by
    # the cdk CLI from whatever AWS credentials are currently active.
    # try_get_context() is checked first, so an explicit
    # `-c account=...` still overrides this if ever needed.
    env=cdk.Environment(
        account=app.node.try_get_context("account") or os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=app.node.try_get_context("region") or os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    ),
    description="Info7375 Week 1 - Shipping Cost Estimator (Strategy + Factory patterns, first CDK deploy)",
)

app.synth()
