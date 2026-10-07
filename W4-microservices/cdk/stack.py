"""
INFO7375 Week 4 -- Microservices Architecture CDK Stack

Topology:
  Internet
      |
  API Gateway (HTTP API)
      |
      |-- /orders/*   -> OrderService    (ECS Fargate, port 8000)
      |-- /products/* -> OrderService    (ECS Fargate, port 8000)
      `-- /health     -> OrderService    (ECS Fargate, port 8000)
      |
  OrderService calls internally:
      |-- PaymentService  (ECS Fargate, port 8001) -- POST /charge
      `-- ShippingService (ECS Fargate, port 8002) -- POST /quote

  Shared infrastructure (same as Week 3):
      `-- RDS PostgreSQL (private subnet, shared by all services)

KEY DESIGN DECISIONS vs Week 3:
  - Three separate ECS Fargate services replace one EC2 instance
  - Each service has its own connection pool (pool isolation)
  - PaymentService and ShippingService are STATELESS - no DB access
  - OrderService owns all database tables (shared DB, not split)
  - AWS X-Ray traces requests across service boundaries
  - Each service has its own CloudWatch log group and alarms
  - Services communicate via internal ALB (not public internet)

WHAT STAYS THE SAME as Week 3:
  - Same RDS PostgreSQL database and schema
  - Same Strategy pattern logic (now running as separate services)
  - Same seed data and load test scenarios
  - Same CloudWatch dashboard structure (extended with service metrics)

SHARED DATABASE NOTE:
  All three services share the same RDS instance. This is the
  intentional "stepping stone" state - services are split but
  data is not. Students feel the awkwardness of this before
  Week 6 splits the data layer properly.
"""
import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_rds as rds,
    aws_elasticloadbalancingv2 as elbv2,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_integrations as integrations,
    aws_iam as iam,
    aws_cloudwatch as cw,
    aws_cloudwatch_actions as cw_actions,
    aws_sns as sns,
    aws_logs as logs,
    aws_xray as xray,
    aws_servicediscovery as sd,
    aws_ecr_assets as ecr_assets,
)
from constructs import Construct


class MicroservicesStack(cdk.Stack):
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        # -- VPC (same structure as Week 3) -----------------------------
        vpc = ec2.Vpc(
            self, "MicroservicesVpc",
            max_azs=2,
            nat_gateways=1,             # needed for ECS Fargate to pull images
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Isolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
        )

        # -- Cloud Map namespace for service discovery -------------------
        # Services find each other by name: http://payment.local:8001
        namespace = sd.PrivateDnsNamespace(
            self, "ServiceNamespace",
            name="info7375.local",
            vpc=vpc,
            description="Week 4 internal service discovery",
        )

        # -- RDS (shared database - same as Week 3) ----------------------
        rds_sg = ec2.SecurityGroup(
            self, "RdsSg", vpc=vpc,
            description="RDS: accept Postgres from ECS services",
            allow_all_outbound=False,
        )

        db_secret = rds.DatabaseSecret(self, "DbSecret", username="appuser")

        db = rds.DatabaseInstance(
            self, "SharedDb",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_15
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T3, ec2.InstanceSize.MICRO
            ),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            security_groups=[rds_sg],
            credentials=rds.Credentials.from_secret(db_secret),
            database_name="microservicesdb",
            multi_az=False,
            allocated_storage=20,
            storage_encrypted=True,
            deletion_protection=False,
            removal_policy=cdk.RemovalPolicy.DESTROY,
            backup_retention=cdk.Duration.days(0),
        )

        # -- ECS Cluster -------------------------------------------------
        cluster = ecs.Cluster(
            self, "MicroservicesCluster",
            vpc=vpc,
            cluster_name="info7375-week4",
            container_insights=True,   # enables CloudWatch Container Insights
        )

        # -- Internal ALB for service-to-service communication -----------
        internal_alb_sg = ec2.SecurityGroup(
            self, "InternalAlbSg", vpc=vpc,
            description="Internal ALB: ECS services talk to each other",
            allow_all_outbound=True,
        )

        internal_alb = elbv2.ApplicationLoadBalancer(
            self, "InternalAlb",
            vpc=vpc,
            internet_facing=False,      # internal only - not public
            security_group=internal_alb_sg,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
        )

        # -- Shared IAM task role -----------------------------------------
        task_role = iam.Role(
            self, "EcsTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AWSXRayDaemonWriteAccess"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "CloudWatchAgentServerPolicy"
                ),
            ],
        )
        db_secret.grant_read(task_role)

        # -- Helper: create a Fargate service ---------------------------
        def make_service(
            name: str,
            port: int,
            cpu: int = 256,
            memory: int = 512,
            desired_count: int = 1,
            environment: dict = {},
        ):
            log_group = logs.LogGroup(
                self, f"{name}LogGroup",
                log_group_name=f"/info7375/week4/{name.lower()}",
                retention=logs.RetentionDays.ONE_WEEK,
                removal_policy=cdk.RemovalPolicy.DESTROY,
            )

            task_def = ecs.FargateTaskDefinition(
                self, f"{name}TaskDef",
                cpu=cpu,
                memory_limit_mib=memory,
                task_role=task_role,
            )

            # X-Ray sidecar - traces cross-service calls
            task_def.add_container(
                "xray-daemon",
                image=ecs.ContainerImage.from_registry(
                    "amazon/aws-xray-daemon"
                ),
                essential=False,
                port_mappings=[ecs.PortMapping(container_port=2000, protocol=ecs.Protocol.UDP)],
                logging=ecs.LogDrivers.aws_logs(
                    stream_prefix="xray",
                    log_group=log_group,
                ),
                memory_reservation_mib=32,
            )

            # Main app container
            container = task_def.add_container(
                name,
                image=ecs.ContainerImage.from_asset(
                    f"../services/{'_'.join([w.lower() for w in __import__('re').findall('[A-Z][a-z]*', name)])}",
                    platform=ecr_assets.Platform.LINUX_AMD64,
                ),
                essential=True,
                port_mappings=[ecs.PortMapping(container_port=port)],
                logging=ecs.LogDrivers.aws_logs(
                    stream_prefix=name.lower(),
                    log_group=log_group,
                ),
                environment={
                    "PORT": str(port),
                    "AWS_XRAY_DAEMON_ADDRESS": "127.0.0.1:2000",
                    "LOG_LEVEL": "INFO",
                    **environment,
                },
                secrets={
                    "DB_SECRET": ecs.Secret.from_secrets_manager(db_secret),
                },
                health_check=ecs.HealthCheck(
                    command=["CMD-SHELL",
                             f"curl -f http://localhost:{port}/health || exit 1"],
                    interval=cdk.Duration.seconds(30),
                    timeout=cdk.Duration.seconds(5),
                    retries=3,
                    start_period=cdk.Duration.seconds(60),
                ),
            )

            svc_sg = ec2.SecurityGroup(
                self, f"{name}Sg", vpc=vpc,
                description=f"{name}: ECS service security group",
                allow_all_outbound=True,
            )
            # Allow from internal ALB
            svc_sg.add_ingress_rule(
                internal_alb_sg,
                ec2.Port.tcp(port),
                f"{name} from internal ALB",
            )

            service = ecs.FargateService(
                self, f"{name}Service",
                cluster=cluster,
                task_definition=task_def,
                desired_count=desired_count,
                security_groups=[svc_sg],
                vpc_subnets=ec2.SubnetSelection(
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
                ),
                assign_public_ip=False,
                service_name=name.lower(),
                cloud_map_options=ecs.CloudMapOptions(
                    cloud_map_namespace=namespace,
                    name=name.lower(),
                ),
                enable_execute_command=True,   # ECS Exec for debugging
            )

            # Allow ECS service to reach RDS
            rds_sg.add_ingress_rule(svc_sg, ec2.Port.tcp(5432),
                                    f"Postgres from {name}")

            return service, svc_sg, log_group

        # -- PaymentService ----------------------------------------------
        payment_service, payment_sg, payment_logs = make_service(
            "PaymentService",
            port=8001,
            cpu=256,
            memory=512,
            environment={
                "PAYMENT_STUB_FAIL_RATE": "0",      # set to 0.2 for Scenario D
                "PAYMENT_STUB_LATENCY_MS": "50",
            },
        )

        # Internal ALB target group for PaymentService
        payment_tg = elbv2.ApplicationTargetGroup(
            self, "PaymentTg",
            vpc=vpc,
            port=8001,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            targets=[payment_service],
            health_check=elbv2.HealthCheck(
                path="/health",
                healthy_http_codes="200",
                interval=cdk.Duration.seconds(30),
            ),
            deregistration_delay=cdk.Duration.seconds(10),
        )

        # -- ShippingService ---------------------------------------------
        shipping_service, shipping_sg, shipping_logs = make_service(
            "ShippingService",
            port=8002,
            cpu=256,
            memory=512,
        )

        shipping_tg = elbv2.ApplicationTargetGroup(
            self, "ShippingTg",
            vpc=vpc,
            port=8002,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            targets=[shipping_service],
            health_check=elbv2.HealthCheck(
                path="/health",
                healthy_http_codes="200",
                interval=cdk.Duration.seconds(30),
            ),
            deregistration_delay=cdk.Duration.seconds(10),
        )

        # -- Internal ALB listeners for payment and shipping -------------
        internal_alb.add_listener(
            "PaymentListener",
            port=8001,
            protocol=elbv2.ApplicationProtocol.HTTP,
            default_target_groups=[payment_tg],
        )
        internal_alb.add_listener(
            "ShippingListener",
            port=8002,
            protocol=elbv2.ApplicationProtocol.HTTP,
            default_target_groups=[shipping_tg],
        )

        # -- OrderService ------------------------------------------------
        order_service, order_sg, order_logs = make_service(
            "OrderService",
            port=8000,
            cpu=512,
            memory=1024,
            environment={
                "PAYMENT_SERVICE_URL": f"http://{internal_alb.load_balancer_dns_name}:8001",
                "SHIPPING_SERVICE_URL": f"http://{internal_alb.load_balancer_dns_name}:8002",
                "DB_POOL_SIZE": "20",
                "DB_POOL_MAX_OVERFLOW": "5",
            },
        )

        # OrderService needs to reach internal ALB (payment + shipping)
        order_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(8000),
            "OrderService from public ALB",
        )
        internal_alb_sg.add_ingress_rule(
            order_sg,
            ec2.Port.tcp(8001),
            "Payment from OrderService",
        )
        internal_alb_sg.add_ingress_rule(
            order_sg,
            ec2.Port.tcp(8002),
            "Shipping from OrderService",
        )

        # -- Public ALB for OrderService ---------------------------------
        public_alb_sg = ec2.SecurityGroup(
            self, "PublicAlbSg", vpc=vpc,
            description="Public ALB: internet to OrderService",
            allow_all_outbound=True,
        )
        public_alb_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "HTTP from internet"
        )

        public_alb = elbv2.ApplicationLoadBalancer(
            self, "PublicAlb",
            vpc=vpc,
            internet_facing=True,
            security_group=public_alb_sg,
        )

        order_tg = elbv2.ApplicationTargetGroup(
            self, "OrderTg",
            vpc=vpc,
            port=8000,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            targets=[order_service],
            health_check=elbv2.HealthCheck(
                path="/health",
                healthy_http_codes="200",
                interval=cdk.Duration.seconds(30),
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
            ),
            deregistration_delay=cdk.Duration.seconds(30),
        )

        public_alb.add_listener(
            "PublicListener",
            port=80,
            default_target_groups=[order_tg],
        )

        # -- CloudWatch alarms -------------------------------------------
        alert_topic = sns.Topic(self, "AlertTopic",
                                display_name="INFO7375-Week4-Alerts")

        # Per-service error rate alarms
        for svc_name, log_group in [
            ("OrderService",    order_logs),
            ("PaymentService",  payment_logs),
            ("ShippingService", shipping_logs),
        ]:
            cw.Alarm(
                self, f"{svc_name}ErrorAlarm",
                alarm_name=f"Week4-{svc_name}-Errors",
                alarm_description=f"{svc_name} error rate elevated",
                metric=cw.Metric(
                    namespace="AWS/ECS",
                    metric_name="CPUUtilization",
                    dimensions_map={
                        "ClusterName": cluster.cluster_name,
                        "ServiceName": svc_name.lower(),
                    },
                    period=cdk.Duration.minutes(1),
                    statistic="Average",
                ),
                threshold=80,
                evaluation_periods=3,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            ).add_alarm_action(cw_actions.SnsAction(alert_topic))

        # Public ALB alarms
        cw.Alarm(
            self, "PublicAlb5xxAlarm",
            alarm_name="Week4-PublicAlb-5xx",
            metric=cw.Metric(
                namespace="AWS/ApplicationELB",
                metric_name="HTTPCode_Target_5XX_Count",
                dimensions_map={"LoadBalancer": public_alb.load_balancer_full_name},
                period=cdk.Duration.minutes(1),
                statistic="Sum",
            ),
            threshold=10,
            evaluation_periods=2,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        ).add_alarm_action(cw_actions.SnsAction(alert_topic))

        cw.Alarm(
            self, "PublicAlbLatencyAlarm",
            alarm_name="Week4-PublicAlb-p99Latency",
            metric=cw.Metric(
                namespace="AWS/ApplicationELB",
                metric_name="TargetResponseTime",
                dimensions_map={"LoadBalancer": public_alb.load_balancer_full_name},
                period=cdk.Duration.minutes(1),
                statistic="p99",
            ),
            threshold=2.0,      # higher than Week 3 - network hops add latency
            evaluation_periods=3,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        ).add_alarm_action(cw_actions.SnsAction(alert_topic))

        # -- CloudWatch Dashboard ----------------------------------------
        dashboard = cw.Dashboard(
            self, "MicroservicesDashboard",
            dashboard_name="INFO7375-Week4-Microservices",
        )

        dashboard.add_widgets(
            # Row 1: public ALB - user-facing health
            cw.GraphWidget(
                title="Public ALB - request count",
                left=[cw.Metric(
                    namespace="AWS/ApplicationELB",
                    metric_name="RequestCount",
                    dimensions_map={"LoadBalancer": public_alb.load_balancer_full_name},
                    period=cdk.Duration.minutes(1), statistic="Sum",
                )],
                width=8,
            ),
            cw.GraphWidget(
                title="Public ALB - p50 / p99 latency (s)",
                left=[
                    cw.Metric(
                        namespace="AWS/ApplicationELB",
                        metric_name="TargetResponseTime",
                        dimensions_map={"LoadBalancer": public_alb.load_balancer_full_name},
                        period=cdk.Duration.minutes(1), statistic="p50", label="p50",
                    ),
                    cw.Metric(
                        namespace="AWS/ApplicationELB",
                        metric_name="TargetResponseTime",
                        dimensions_map={"LoadBalancer": public_alb.load_balancer_full_name},
                        period=cdk.Duration.minutes(1), statistic="p99", label="p99",
                    ),
                ],
                width=8,
            ),
            cw.GraphWidget(
                title="Public ALB - 5xx errors",
                left=[cw.Metric(
                    namespace="AWS/ApplicationELB",
                    metric_name="HTTPCode_Target_5XX_Count",
                    dimensions_map={"LoadBalancer": public_alb.load_balancer_full_name},
                    period=cdk.Duration.minutes(1), statistic="Sum",
                )],
                width=8,
            ),
            # Row 2: per-service CPU
            cw.GraphWidget(
                title="ECS CPU - all services (%)",
                left=[
                    cw.Metric(
                        namespace="AWS/ECS",
                        metric_name="CPUUtilization",
                        dimensions_map={"ClusterName": cluster.cluster_name,
                                        "ServiceName": "orderservice"},
                        period=cdk.Duration.minutes(1), statistic="Average",
                        label="OrderService",
                    ),
                    cw.Metric(
                        namespace="AWS/ECS",
                        metric_name="CPUUtilization",
                        dimensions_map={"ClusterName": cluster.cluster_name,
                                        "ServiceName": "paymentservice"},
                        period=cdk.Duration.minutes(1), statistic="Average",
                        label="PaymentService",
                    ),
                    cw.Metric(
                        namespace="AWS/ECS",
                        metric_name="CPUUtilization",
                        dimensions_map={"ClusterName": cluster.cluster_name,
                                        "ServiceName": "shippingservice"},
                        period=cdk.Duration.minutes(1), statistic="Average",
                        label="ShippingService",
                    ),
                ],
                width=12,
            ),
            # Row 2: per-service memory
            cw.GraphWidget(
                title="ECS Memory - all services (%)",
                left=[
                    cw.Metric(
                        namespace="AWS/ECS",
                        metric_name="MemoryUtilization",
                        dimensions_map={"ClusterName": cluster.cluster_name,
                                        "ServiceName": "orderservice"},
                        period=cdk.Duration.minutes(1), statistic="Average",
                        label="OrderService",
                    ),
                    cw.Metric(
                        namespace="AWS/ECS",
                        metric_name="MemoryUtilization",
                        dimensions_map={"ClusterName": cluster.cluster_name,
                                        "ServiceName": "paymentservice"},
                        period=cdk.Duration.minutes(1), statistic="Average",
                        label="PaymentService",
                    ),
                    cw.Metric(
                        namespace="AWS/ECS",
                        metric_name="MemoryUtilization",
                        dimensions_map={"ClusterName": cluster.cluster_name,
                                        "ServiceName": "shippingservice"},
                        period=cdk.Duration.minutes(1), statistic="Average",
                        label="ShippingService",
                    ),
                ],
                width=12,
            ),
            # Row 3: RDS
            cw.GraphWidget(
                title="RDS CPU utilisation (%)",
                left=[cw.Metric(
                    namespace="AWS/RDS",
                    metric_name="CPUUtilization",
                    dimensions_map={"DBInstanceIdentifier": db.instance_identifier},
                    period=cdk.Duration.minutes(1), statistic="Average",
                )],
                width=8,
            ),
            cw.GraphWidget(
                title="RDS DB connections (shared pool)",
                left=[cw.Metric(
                    namespace="AWS/RDS",
                    metric_name="DatabaseConnections",
                    dimensions_map={"DBInstanceIdentifier": db.instance_identifier},
                    period=cdk.Duration.minutes(1), statistic="Average",
                )],
                width=8,
            ),
            cw.GraphWidget(
                title="Internal ALB - payment + shipping latency",
                left=[
                    cw.Metric(
                        namespace="AWS/ApplicationELB",
                        metric_name="TargetResponseTime",
                        dimensions_map={"LoadBalancer": internal_alb.load_balancer_full_name,
                                        "TargetGroup": payment_tg.target_group_full_name},
                        period=cdk.Duration.minutes(1), statistic="p99",
                        label="payment p99",
                    ),
                    cw.Metric(
                        namespace="AWS/ApplicationELB",
                        metric_name="TargetResponseTime",
                        dimensions_map={"LoadBalancer": internal_alb.load_balancer_full_name,
                                        "TargetGroup": shipping_tg.target_group_full_name},
                        period=cdk.Duration.minutes(1), statistic="p99",
                        label="shipping p99",
                    ),
                ],
                width=8,
            ),
        )

        # -- Outputs -----------------------------------------------------
        cdk.CfnOutput(self, "PublicAlbDns",
                      value=public_alb.load_balancer_dns_name,
                      description="Public endpoint - same interface as Week 3")
        cdk.CfnOutput(self, "InternalAlbDns",
                      value=internal_alb.load_balancer_dns_name,
                      description="Internal ALB - payment and shipping endpoints")
        cdk.CfnOutput(self, "ClusterName",
                      value=cluster.cluster_name,
                      description="ECS cluster name")
        cdk.CfnOutput(self, "DbSecretArn",
                      value=db_secret.secret_arn,
                      description="Shared DB credentials")
        cdk.CfnOutput(self, "DbEndpoint",
                      value=db.db_instance_endpoint_address,
                      description="RDS endpoint")
        cdk.CfnOutput(self, "DashboardUrl",
                      value=f"https://{self.region}.console.aws.amazon.com/cloudwatch/home"
                            f"#dashboards:name=INFO7375-Week4-Microservices",
                      description="CloudWatch dashboard")
        cdk.CfnOutput(self, "XRayUrl",
                      value=f"https://{self.region}.console.aws.amazon.com/xray/home#/traces",
                      description="X-Ray service map - cross-service traces")
