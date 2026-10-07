"""
stack.py - Week 2: adding persistence.

Extends Week 1's two-resource stack with one DynamoDB table and one
new route. The Lambda, the HTTP API, and the original POST /estimate
route are unchanged in shape - the new work is entirely in what the
Lambda's code does (see repository.py), not in how it is wired up.
"""

import os
from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    RemovalPolicy,
    aws_lambda as _lambda,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_integrations as apigwv2_integrations,
    aws_dynamodb as dynamodb,
    aws_cloudwatch as cloudwatch,
)
from constructs import Construct

LAMBDA_DIR = os.path.join(os.path.dirname(__file__), "..", "lambda")


class ShippingEstimatorStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # -------------------------------------------------------------
        # DynamoDB - on-demand billing, no VPC required. Deliberately
        # DESTROY, not the CDK default RETAIN for stateful resources -
        # this is course lab data, not production data, and RemovalPolicy
        # was the exact thing that orphaned a Kinesis stream in an
        # earlier week's stack when left at its default.
        # -------------------------------------------------------------
        quotes_table = dynamodb.Table(
            self,
            "QuotesTable",
            partition_key=dynamodb.Attribute(name="quote_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        estimator_fn = _lambda.Function(
            self,
            "ShippingEstimatorFunction",
            runtime=_lambda.Runtime.PYTHON_3_10,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset(LAMBDA_DIR),
            timeout=Duration.seconds(10),
            memory_size=128,
            environment={
                "QUOTES_TABLE": quotes_table.table_name,
            },
        )
        quotes_table.grant_read_write_data(estimator_fn)

        http_api = apigwv2.HttpApi(
            self,
            "ShippingEstimatorApi",
            description="Week 2 - Shipping Cost Estimator + Quotes",
        )

        integration = apigwv2_integrations.HttpLambdaIntegration(
            "EstimatorIntegration", estimator_fn
        )

        http_api.add_routes(
            path="/estimate",
            methods=[apigwv2.HttpMethod.POST],
            integration=integration,
        )
        http_api.add_routes(
            path="/quotes/{quote_id}",
            methods=[apigwv2.HttpMethod.GET],
            integration=integration,
        )

        CfnOutput(self, "ApiEndpoint", value=http_api.api_endpoint)
        CfnOutput(self, "FunctionName", value=estimator_fn.function_name)
        CfnOutput(self, "QuotesTableName", value=quotes_table.table_name)

        # -------------------------------------------------------------
        # CloudWatch dashboard - extends Week 1's two widgets with two
        # DynamoDB-specific ones, rather than being rebuilt from
        # scratch. On-demand billing means there is no "consumed
        # capacity vs. provisioned capacity" story to show - the
        # meaningful DynamoDB signals here are latency and throttling.
        # -------------------------------------------------------------
        dashboard = cloudwatch.Dashboard(
            self, "EstimatorDashboard", dashboard_name="Info7375-Week2-ShippingEstimator"
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
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="DynamoDB - request latency",
                left=[
                    quotes_table.metric_successful_request_latency(
                        dimensions_map={"TableName": quotes_table.table_name, "Operation": "GetItem"}
                    ),
                    quotes_table.metric_successful_request_latency(
                        dimensions_map={"TableName": quotes_table.table_name, "Operation": "PutItem"}
                    ),
                ],
                width=12,
            ),
            cloudwatch.GraphWidget(
                title="DynamoDB - throttled requests (should stay at 0)",
                left=[quotes_table.metric_throttled_requests_for_operations(
                    operations=[dynamodb.Operation.GET_ITEM, dynamodb.Operation.PUT_ITEM]
                )],
                width=12,
            ),
        )

        CfnOutput(
            self,
            "DashboardUrl",
            value="https://{}.console.aws.amazon.com/cloudwatch/home?region={}#dashboards:name=Info7375-Week2-ShippingEstimator".format(
                self.region, self.region
            ),
        )
