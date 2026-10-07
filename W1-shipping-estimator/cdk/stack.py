"""
stack.py - Week 1: first CDK deploy.

Two resources, on purpose: one Lambda function, one API Gateway HTTP
API with a single route. This is the smallest possible "real" AWS
deployment - small enough to read top to bottom in one sitting, real
enough that everything you see here (the auto-generated IAM role, the
Lambda integration, the route) is the same shape you will use again,
with more moving parts, for every service this course builds
afterward.

Why HTTP API and not REST API: CDK's HttpApi construct is simpler and
faster to deploy than the older, more feature-rich RestApi construct.
REST API's extra features (usage plans, request validators, WAF
integration, etc.) are not worth the added complexity for a first
exercise - they matter more once a service actually needs them.
"""

import os
from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    aws_lambda as _lambda,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_integrations as apigwv2_integrations,
    aws_cloudwatch as cloudwatch,
)
from constructs import Construct

LAMBDA_DIR = os.path.join(os.path.dirname(__file__), "..", "lambda")


class ShippingEstimatorStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        estimator_fn = _lambda.Function(
            self,
            "ShippingEstimatorFunction",
            runtime=_lambda.Runtime.PYTHON_3_10,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset(LAMBDA_DIR),
            timeout=Duration.seconds(10),
            memory_size=128,
        )

        http_api = apigwv2.HttpApi(
            self,
            "ShippingEstimatorApi",
            description="Week 1 - Shipping Cost Estimator",
        )

        http_api.add_routes(
            path="/estimate",
            methods=[apigwv2.HttpMethod.POST],
            integration=apigwv2_integrations.HttpLambdaIntegration(
                "EstimatorIntegration", estimator_fn
            ),
        )

        CfnOutput(self, "ApiEndpoint", value="{}/estimate".format(http_api.api_endpoint))
        CfnOutput(self, "FunctionName", value=estimator_fn.function_name)

        # -------------------------------------------------------------
        # CloudWatch dashboard - one shared URL, built ahead of time,
        # rather than bouncing between the Lambda and API Gateway
        # consoles' separate Monitor tabs during a live class load
        # test. Two widgets: Lambda's own view (did the function run,
        # did it error) and API Gateway's view (did the request even
        # reach the function, how fast did the whole round-trip take) -
        # deliberately kept as two SEPARATE vantage points rather than
        # merged, since a request that never reaches the Lambda at all
        # (e.g. malformed at the HTTP layer) would only show up here.
        # -------------------------------------------------------------
        dashboard = cloudwatch.Dashboard(
            self, "EstimatorDashboard", dashboard_name="Info7375-Week1-ShippingEstimator"
        )
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Lambda - invocations vs. errors",
                left=[estimator_fn.metric_invocations()],
                right=[estimator_fn.metric_errors()],
                width=12,
            ),
            cloudwatch.GraphWidget(
                title="API Gateway - requests vs. latency",
                left=[http_api.metric_count()],
                right=[http_api.metric_latency()],
                width=12,
            ),
        )

        CfnOutput(
            self,
            "DashboardUrl",
            value="https://{}.console.aws.amazon.com/cloudwatch/home?region={}#dashboards:name=Info7375-Week1-ShippingEstimator".format(
                self.region, self.region
            ),
        )
