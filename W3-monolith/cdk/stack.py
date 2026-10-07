import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_rds as rds,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_cloudwatch as cw,
    aws_cloudwatch_actions as cw_actions,
    aws_sns as sns,
    aws_logs as logs,
    aws_codedeploy as codedeploy,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_elasticloadbalancingv2_targets as targets,
    aws_iam as iam,
)
from constructs import Construct


class MonolithStack(cdk.Stack):
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        # -- VPC ------------------------------------------------------------
        # Two public subnets (EC2 + ALB), two private subnets (RDS).
        # No NAT Gateway — lab cost constraint.
        vpc = ec2.Vpc(
            self, "MonolithVpc",
            max_azs=2,
            nat_gateways=0,                         # cost: ~$0/day (no NAT)
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
        )

        # -- Security groups ------------------------------------------------
        alb_sg = ec2.SecurityGroup(
            self, "AlbSg",
            vpc=vpc,
            description="ALB: accept HTTPS from internet",
            allow_all_outbound=True,
        )
        alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "HTTP from internet")
        alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "HTTPS from internet")

        ec2_sg = ec2.SecurityGroup(
            self, "Ec2Sg",
            vpc=vpc,
            description="EC2: accept traffic from ALB only",
            allow_all_outbound=True,
        )
        ec2_sg.add_ingress_rule(alb_sg, ec2.Port.tcp(8000), "FastAPI from ALB")
        ec2_sg.add_ingress_rule(
            ec2.Peer.any_ipv4(), ec2.Port.tcp(22), "SSH for lab debugging"
        )

        rds_sg = ec2.SecurityGroup(
            self, "RdsSg",
            vpc=vpc,
            description="RDS: accept Postgres from EC2 only",
            allow_all_outbound=False,
        )
        rds_sg.add_ingress_rule(ec2_sg, ec2.Port.tcp(5432), "Postgres from EC2")

        # -- RDS PostgreSQL -------------------------------------------------
        db_secret = rds.DatabaseSecret(self, "DbSecret", username="appuser")

        db = rds.DatabaseInstance(
            self, "MonolithDb",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_15
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T3, ec2.InstanceSize.MICRO  # ~$0.02/hr
            ),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            security_groups=[rds_sg],
            credentials=rds.Credentials.from_secret(db_secret),
            database_name="monolithdb",
            multi_az=False,                         # single-AZ: lab cost constraint
            allocated_storage=20,
            storage_encrypted=True,
            deletion_protection=False,              # easy teardown for lab
            removal_policy=cdk.RemovalPolicy.DESTROY,
            backup_retention=cdk.Duration.days(0),  # no backups: lab cost constraint
            enable_performance_insights=False,
            cloudwatch_logs_exports=["postgresql"],
        )

        # -- IAM role for EC2 -----------------------------------------------
        ec2_role = iam.Role(
            self, "Ec2Role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "CloudWatchAgentServerPolicy"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonSSMManagedInstanceCore"
                ),
                # CodeDeploy agent needs S3 read to pull the revision bundle
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonS3ReadOnlyAccess"
                ),
            ],
        )

        # Allow EC2 to read the DB secret
        db_secret.grant_read(ec2_role)

        #~dk: The instance role needs cloudformation:DescribeStacks permission, not 
        # just secretsmanager:GetSecretValue
        ec2_role.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudformation:DescribeStacks"],
                resources=[f"arn:aws:cloudformation:{self.region}:{self.account}:stack/Week3MonolithStack/*"],
            )
        )

        # -- CloudWatch Log Group -------------------------------------------
        app_log_group = logs.LogGroup(
            self, "AppLogs",
            log_group_name="/info7375/week3/app",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # -- EC2 user-data bootstrap ----------------------------------------
        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            # System updates
            "yum update -y",
            "yum install -y python3.11 python3.11-pip postgresql15 git jq ruby wget",

            # Install CloudWatch agent
            "yum install -y amazon-cloudwatch-agent",

            # Install CodeDeploy agent
            f"wget https://aws-codedeploy-{self.region}.s3.{self.region}.amazonaws.com"
            f"/latest/install -O /tmp/codedeploy-install",
            "chmod +x /tmp/codedeploy-install",
            "/tmp/codedeploy-install auto",
            "systemctl enable codedeploy-agent",
            "systemctl start codedeploy-agent",

            # App directory
            "mkdir -p /opt/app/logs",

            # Pre-install Python deps so CodeDeploy AfterInstall hook is fast
            "pip3.11 install fastapi uvicorn[standard] psycopg2-binary "
            "sqlalchemy alembic boto3 python-json-logger cachetools stripe",

            # Write CloudWatch agent config
            """cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json << 'CWEOF'
{
  "agent": { "metrics_collection_interval": 60, "run_as_user": "root" },
  "metrics": {
    "namespace": "Info7375/Week3",
    "metrics_collected": {
      "cpu": { "measurement": ["cpu_usage_active"], "totalcpu": true },
      "mem": { "measurement": ["mem_used_percent"] },
      "disk": { "measurement": ["disk_used_percent"], "resources": ["/"] }
    }
  },
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [{
          "file_path": "/opt/app/logs/app.log",
          "log_group_name": "/info7375/week3/app",
          "log_stream_name": "{instance_id}",
          "timestamp_format": "%Y-%m-%dT%H:%M:%S"
        }]
      }
    }
  }
}
CWEOF""",
            "/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl "
            "-a fetch-config -m ec2 -s "
            "-c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json",

            # Fetch DB credentials from Secrets Manager
            f"SECRET=$(aws secretsmanager get-secret-value "
            f"--secret-id {db_secret.secret_arn} "
            f"--region {self.region} --query SecretString --output text)",
            "DB_HOST=$(echo $SECRET | jq -r .host)",
            "DB_PORT=$(echo $SECRET | jq -r .port)",
            "DB_NAME=$(echo $SECRET | jq -r .dbname)",
            "DB_USER=$(echo $SECRET | jq -r .username)",
            "DB_PASS=$(echo $SECRET | jq -r .password)",

            # Write .env
            "cat > /opt/app/.env << ENVEOF",
            "DATABASE_URL=postgresql://${DB_USER}:${DB_PASS}@${DB_HOST}:${DB_PORT}/${DB_NAME}",
            "DB_POOL_SIZE=20",
            "DB_POOL_MAX_OVERFLOW=5",
            f"AWS_REGION={self.region}",
            "LOG_LEVEL=INFO",
            "ENVEOF",

            # Systemd service
            """cat > /etc/systemd/system/monolith.service << 'SVCEOF'
[Unit]
Description=Info7375 Week3 Monolith App
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/app
EnvironmentFile=/opt/app/.env
ExecStart=/usr/local/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF""",
            "systemctl daemon-reload",
            # CodeDeploy will deploy app files and start the service after first deploy
        )

        # -- EC2 instance ---------------------------------------------------
        instance = ec2.Instance(
            self, "MonolithEc2",
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T3, ec2.InstanceSize.MEDIUM  # 2 vCPU, 4 GB
            ),
            machine_image=ec2.AmazonLinuxImage(
                generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2023
            ),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PUBLIC
            ),
            security_group=ec2_sg,
            role=ec2_role,
            user_data=user_data,
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(
                        20, volume_type=ec2.EbsDeviceVolumeType.GP3
                    ),
                )
            ],
        )


        # -- ALB -----------------------------------------------------------
        alb = elbv2.ApplicationLoadBalancer(
            self, "MonolithAlb",
            vpc=vpc,
            internet_facing=True,
            security_group=alb_sg,
        )

        target_group = elbv2.ApplicationTargetGroup(
            self, "MonolithTg",
            vpc=vpc,
            port=8000,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[targets.InstanceTarget(instance, 8000)],
            health_check=elbv2.HealthCheck(
                path="/health",
                healthy_http_codes="200",
                interval=cdk.Duration.seconds(30),
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
                timeout=cdk.Duration.seconds(5),
            ),
            deregistration_delay=cdk.Duration.seconds(30),
        )

        # HTTP listener (lab uses HTTP for simplicity — HTTPS needs ACM cert)
        alb.add_listener(
            "HttpListener",
            port=80,
            default_target_groups=[target_group],
        )

        # -- CloudWatch alarms ----------------------------------------------
        alert_topic = sns.Topic(self, "AlertTopic",
                                display_name="Info7375-Week3-Alerts")

        # High CPU — Break scenario early warning
        cw.Alarm(
            self, "HighCpuAlarm",
            metric=cw.Metric(
                namespace="AWS/EC2",
                metric_name="CPUUtilization",
                dimensions_map={"InstanceId": instance.instance_id},
                period=cdk.Duration.minutes(1),
                statistic="Average",
            ),
            threshold=80,
            evaluation_periods=3,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="EC2 CPU > 80% for 3 min",
            alarm_name="Week3-HighCPU",
        ).add_alarm_action(cw_actions.SnsAction(alert_topic))

        # ALB 5xx errors — main user-visible failure signal
        cw.Alarm(
            self, "Alb5xxAlarm",
            metric=cw.Metric(
                namespace="AWS/ApplicationELB",
                metric_name="HTTPCode_Target_5XX_Count",
                dimensions_map={"LoadBalancer": alb.load_balancer_full_name},
                period=cdk.Duration.minutes(1),
                statistic="Sum",
            ),
            threshold=10,
            evaluation_periods=2,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="ALB 5xx errors > 10 in 1 min — users seeing errors",
            alarm_name="Week3-5xxErrors",
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        ).add_alarm_action(cw_actions.SnsAction(alert_topic))

        # ALB response time — p99 latency signal
        cw.Alarm(
            self, "AlbLatencyAlarm",
            metric=cw.Metric(
                namespace="AWS/ApplicationELB",
                metric_name="TargetResponseTime",
                dimensions_map={"LoadBalancer": alb.load_balancer_full_name},
                period=cdk.Duration.minutes(1),
                statistic="p99",
            ),
            threshold=1.0,          # 1 second p99 = degraded
            evaluation_periods=3,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            alarm_description="p99 latency > 1s for 3 min",
            alarm_name="Week3-HighLatency",
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        ).add_alarm_action(cw_actions.SnsAction(alert_topic))

        # -- CloudWatch Dashboard -------------------------------------------
        dashboard = cw.Dashboard(self, "MonolithDashboard",
                                 dashboard_name="Info7375-Week3-Monolith")
        dashboard.add_widgets(
            cw.GraphWidget(
                title="EC2 CPU utilisation (%)",
                left=[cw.Metric(
                    namespace="AWS/EC2", metric_name="CPUUtilization",
                    dimensions_map={"InstanceId": instance.instance_id},
                    period=cdk.Duration.minutes(1), statistic="Average",
                )],
                width=8,
            ),
            cw.GraphWidget(
                title="ALB request count",
                left=[cw.Metric(
                    namespace="AWS/ApplicationELB",
                    metric_name="RequestCount",
                    dimensions_map={"LoadBalancer": alb.load_balancer_full_name},
                    period=cdk.Duration.minutes(1), statistic="Sum",
                )],
                width=8,
            ),
            cw.GraphWidget(
                title="ALB p50 / p99 latency (s)",
                left=[
                    cw.Metric(
                        namespace="AWS/ApplicationELB",
                        metric_name="TargetResponseTime",
                        dimensions_map={"LoadBalancer": alb.load_balancer_full_name},
                        period=cdk.Duration.minutes(1), statistic="p50",
                        label="p50",
                    ),
                    cw.Metric(
                        namespace="AWS/ApplicationELB",
                        metric_name="TargetResponseTime",
                        dimensions_map={"LoadBalancer": alb.load_balancer_full_name},
                        period=cdk.Duration.minutes(1), statistic="p99",
                        label="p99",
                    ),
                ],
                width=8,
            ),
            cw.GraphWidget(
                title="ALB 5xx errors",
                left=[cw.Metric(
                    namespace="AWS/ApplicationELB",
                    metric_name="HTTPCode_Target_5XX_Count",
                    dimensions_map={"LoadBalancer": alb.load_balancer_full_name},
                    period=cdk.Duration.minutes(1), statistic="Sum",
                )],
                width=8,
            ),
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
                title="RDS DB connections",
                left=[cw.Metric(
                    namespace="AWS/RDS",
                    metric_name="DatabaseConnections",
                    dimensions_map={"DBInstanceIdentifier": db.instance_identifier},
                    period=cdk.Duration.minutes(1), statistic="Average",
                )],
                width=8,
            ),
        )

        # -- S3 artifact bucket ---------------------------------------------
        # Students push app bundles here; CodeDeploy pulls from here.
        artifact_bucket = s3.Bucket(
            self, "ArtifactBucket",
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            versioned=True,                         # CodeDeploy needs versioning
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )
        artifact_bucket.grant_read(ec2_role)

        # NOTE: Manual app bundle upload required after cdk deploy.
        #
        # BACKGROUND: CDK's BucketDeployment construct was removed from this stack
        # because of a known incompatibility in CDK 2.155.0. The BucketDeployment
        # Lambda is hardcoded to run on Python 3.9, but the AWS CLI bundled inside
        # that Lambda was built with urllib3 that uses the X | Y type union syntax
        # (e.g. bytes | str) introduced in Python 3.10. This causes a TypeError at
        # runtime when the Lambda tries to run 'aws s3 cp' to copy the app bundle
        # from the CDK bootstrap assets bucket to the artifact bucket.
        #
        # CDK escape hatch (add_override) was attempted to change the Lambda runtime
        # to python3.12 but the child node path was not stable across synth runs.
        # Re-bootstrapping (cdk bootstrap --force) did not help since bootstrap
        # version 32 is already current. Downgrading aws-cdk-lib was rejected to
        # avoid breaking other course weeks that depend on 2.155.0.
        #
        # WORKAROUND: After cdk deploy completes, run:
        #   cd ../app
        #   zip -r /tmp/app.zip . \
        #     --exclude "*/__pycache__/*" \
        #     --exclude "*.pyc" \
        #     --exclude "logs/*" \
        #     --exclude ".DS_Store"
        #   export BUCKET=$(aws cloudformation describe-stacks \
        #     --stack-name Week3MonolithStack \
        #     --query 'Stacks[0].Outputs[?OutputKey==`ArtifactBucketName`].OutputValue' \
        #     --output text)
        #   aws s3 cp /tmp/app.zip s3://$BUCKET/app/app.zip

        # REMOVED :-( TOO BAD, WOULD'VE BEEN COOL. MAY STILL WORK ON YOUR END IF
        # YOU HAVE THE RIGHT VERSIONS OF EVERYTHING, BUT TOO RISKY
        #bucket_deployment_role = iam.Role(
        #    self, "BucketDeploymentRole",
        #    assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        #    managed_policies=[
        #        iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3FullAccess"),
        #        iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
        #    ],
        #)
        #
        # Upload the initial app bundle so first deploy works immediately
        #initial_deployment = s3deploy.BucketDeployment(
        #    self, "InitialAppBundle",
        #    sources=[s3deploy.Source.asset("../app")],
        #    destination_bucket=artifact_bucket,
        #    destination_key_prefix="app/",
        #    role=bucket_deployment_role,
        #    memory_limit=512,
        #)
        #
        ## Escape hatch: override BucketDeployment Lambda runtime from python3.9 to python3.12
        ## CDK 2.155.0 hardcodes python3.9 but the bundled AWS CLI requires python3.10+
        #cfn_function = initial_deployment.node.find_child(
        #    "CustomCDKBucketDeployment8693BB64968944B69AAFB0CC9EB8756C512MiB6723FB92"
        #)
        #cfn_function.add_override("Properties.Runtime", "python3.12")


        # -- CodeDeploy -----------------------------------------------------
        codedeploy_role = iam.Role(
            self, "CodeDeployRole",
            assumed_by=iam.ServicePrincipal("codedeploy.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSCodeDeployRole"
                ),
            ],
        )

        cd_app = codedeploy.ServerApplication(
            self, "MonolithApp",
            application_name="info7375-week3-monolith",
        )

        deployment_group = codedeploy.ServerDeploymentGroup(
            self, "MonolithDG",
            application=cd_app,
            deployment_group_name="week3-monolith-dg",
            role=codedeploy_role,
            ec2_instance_tags=codedeploy.InstanceTagSet(
                {"aws:cloudformation:stack-name": [self.stack_name]}
            ),
            # In-place deployment — stop app, deploy, restart
            deployment_config=codedeploy.ServerDeploymentConfig.ONE_AT_A_TIME,
            # Auto-rollback on failure
            auto_rollback=codedeploy.AutoRollbackConfig(
                failed_deployment=True,
                stopped_deployment=True,
            ),
            # Hook the ALB so CodeDeploy deregisters target during deploy
            load_balancers=[
                codedeploy.LoadBalancer.application(target_group)
            ],
        )

        # -- Outputs --------------------------------------------------------
        cdk.CfnOutput(self, "AlbDns",
                      value=alb.load_balancer_dns_name,
                      description="Paste into load-test.yml and curl /health")
        cdk.CfnOutput(self, "Ec2InstanceId",
                      value=instance.instance_id,
                      description="EC2 instance - use for SSM Session Manager")
        cdk.CfnOutput(self, "DbSecretArn",
                      value=db_secret.secret_arn,
                      description="Read DB credentials via aws secretsmanager get-secret-value")
        cdk.CfnOutput(self, "DbEndpoint",
                      value=db.db_instance_endpoint_address,
                      description="RDS endpoint - reachable from EC2 only")
        cdk.CfnOutput(self, "ArtifactBucketName",
                      value=artifact_bucket.bucket_name,
                      description="Push app bundles here for CodeDeploy")
        cdk.CfnOutput(self, "CodeDeployAppName",
                      value=cd_app.application_name,
                      description="CodeDeploy application name")
        cdk.CfnOutput(self, "CodeDeployDGName",
                      value=deployment_group.deployment_group_name,
                      description="CodeDeploy deployment group name")
        cdk.CfnOutput(self, "DashboardUrl",
                      value=f"https://{self.region}.console.aws.amazon.com/cloudwatch/home"
                            f"#dashboards:name=Info7375-Week3-Monolith",
                      description="CloudWatch dashboard")
