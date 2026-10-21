# Pure mock plans: no AWS credentials, infrastructure creation or remote API calls.
mock_provider "aws" {
  override_during = plan
  mock_data "aws_subnet" {
    defaults = { vpc_id = "vpc-0123456789abcdef0", availability_zone = "ap-northeast-2a", cidr_block = "10.0.1.0/24" }
  }
  mock_data "aws_ssm_parameter" {
    defaults = { value = "ami-0123456789abcdef0" }
  }
  mock_data "aws_ec2_instance_type" {
    defaults = { supported_architectures = ["x86_64"], memory_size = 8192, default_vcpus = 2, ebs_nvme_support = "required" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/test-workbench" }
  }
  mock_resource "aws_lb" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:123456789012:loadbalancer/app/workbench/1234567890123456" }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:123456789012:targetgroup/workbench/1234567890123456" }
  }
  mock_resource "aws_ecs_cluster" {
    defaults = { arn = "arn:aws:ecs:ap-northeast-2:123456789012:cluster/workbench", id = "arn:aws:ecs:ap-northeast-2:123456789012:cluster/workbench" }
  }
  mock_resource "aws_ecs_task_definition" {
    defaults = { arn = "arn:aws:ecs:ap-northeast-2:123456789012:task-definition/workbench:1" }
  }
  mock_resource "aws_security_group" {
    defaults = { id = "sg-0123456789abcdef0" }
  }
  mock_resource "aws_instance" {
    defaults = { id = "i-0123456789abcdef0" }
  }
  mock_resource "aws_ebs_volume" {
    defaults = { id = "vol-0123456789abcdef0" }
  }
}
# ALB must span distinct AZs; the node is in AZ a.
override_data {
  target = data.aws_subnet.alb["subnet-0123456789abcdef1"]
  values = { vpc_id = "vpc-0123456789abcdef0", availability_zone = "ap-northeast-2c", cidr_block = "10.0.2.0/24" }
}
variables {
  vpc_id               = "vpc-0123456789abcdef0"
  alb_subnet_ids       = ["subnet-0123456789abcdef0", "subnet-0123456789abcdef1"]
  instance_subnet_id   = "subnet-0123456789abcdef2"
  allowed_client_cidrs = ["10.10.0.0/16"]
  certificate_arn      = "arn:aws:acm:ap-northeast-2:123456789012:certificate/12345678-1234-1234-1234-123456789012"
  token_secret_arn     = "arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:riotx/token-ABC123"
  container_image      = "123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/riotx-workbench@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
run "single_writer_persistent_https_deployment" {
  command = plan
  assert {
    condition     = aws_ecs_service.app.desired_count == 1 && aws_ecs_service.app.deployment_minimum_healthy_percent == 0 && aws_ecs_service.app.deployment_maximum_percent == 100
    error_message = "SQLite must use a single writer with stop-first deployment."
  }
  assert {
    condition     = aws_ebs_volume.data.encrypted && !aws_instance.node.associate_public_ip_address && aws_lb.app.internal
    error_message = "Persistent storage must be encrypted and the default network private."
  }
  assert {
    condition     = aws_lb_listener.https.protocol == "HTTPS" && aws_lb_listener.https.port == 443
    error_message = "ALB must only expose HTTPS."
  }
  assert {
    condition     = aws_ecs_task_definition.app.network_mode == "host" && one(aws_ecs_task_definition.app.volume).host_path == "/var/lib/riotx-data"
    error_message = "SQLite WAL must use the local EBS-backed host directory."
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.app.container_definitions)[0].secrets[0].valueFrom == var.token_secret_arn && !contains([for e in jsondecode(aws_ecs_task_definition.app.container_definitions)[0].environment : e.name], "WORKBENCH_TOKEN")
    error_message = "The token must come from Secrets Manager, not plaintext environment values."
  }
}
run "reject_open_internet_ingress" {
  command = plan
  variables { allowed_client_cidrs = ["0.0.0.0/0"] }
  expect_failures = [var.allowed_client_cidrs]
}
run "reject_mutable_image" {
  command = plan
  variables { container_image = "example.com/workbench:latest" }
  expect_failures = [var.container_image]
}
run "reject_wrong_instance_architecture" {
  command = plan
  variables { architecture = "ARM64" }
  expect_failures = [aws_instance.node]
}
run "reject_insufficient_memory" {
  command = plan
  variables { task_memory = 8192 }
  expect_failures = [aws_instance.node]
}
run "explicit_redis_access" {
  command = plan
  variables { redis_security_group_ids = ["sg-0123456789abcdef1"] }
  assert {
    condition     = length(aws_security_group_rule.redis) == 1 && aws_security_group_rule.redis["sg-0123456789abcdef1"].from_port == 6379
    error_message = "Opt-in Redis access must be scoped to the requested SG and port."
  }
}
run "arm64_deployment" {
  command = plan
  variables {
    architecture  = "ARM64"
    instance_type = "t4g.large"
  }
  override_data {
    target = data.aws_ec2_instance_type.node
    values = { supported_architectures = ["arm64"], memory_size = 8192, default_vcpus = 2, ebs_nvme_support = "required" }
  }
  assert {
    condition     = one(aws_ecs_task_definition.app.runtime_platform).cpu_architecture == "ARM64" && strcontains(data.aws_ssm_parameter.ami.name, "/arm64/")
    error_message = "ARM64 images must select the ARM64 task platform and optimized AMI."
  }
}
run "reject_non_nvme_host" {
  command = plan
  override_data {
    target = data.aws_ec2_instance_type.node
    values = { supported_architectures = ["x86_64"], memory_size = 8192, default_vcpus = 2, ebs_nvme_support = "unsupported" }
  }
  expect_failures = [aws_instance.node]
}
