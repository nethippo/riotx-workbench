data "aws_ec2_instance_type" "node" { instance_type = var.instance_type }
data "aws_subnet" "node" { id = var.instance_subnet_id }
data "aws_subnet" "alb" {
  for_each = toset(var.alb_subnet_ids)
  id       = each.value
}
data "aws_ssm_parameter" "ami" {
  name = var.architecture == "ARM64" ? "/aws/service/ecs/optimized-ami/amazon-linux-2023/arm64/recommended/image_id" : "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id"
}
locals {
  trust = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_ecs_cluster" "app" {
  name = var.name
}
resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.name}"
  retention_in_days = var.log_retention_days
}
resource "aws_security_group" "alb" {
  name_prefix = "${var.name}-alb-"
  vpc_id      = var.vpc_id
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.allowed_client_cidrs
  }
  egress {
    from_port   = 8088
    to_port     = 8088
    protocol    = "tcp"
    cidr_blocks = [data.aws_subnet.node.cidr_block]
  }
}
resource "aws_security_group" "node" {
  name_prefix = "${var.name}-node-"
  vpc_id      = var.vpc_id
  ingress {
    from_port       = 8088
    to_port         = 8088
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
resource "aws_security_group_rule" "redis" {
  for_each                 = var.redis_security_group_ids
  type                     = "ingress"
  security_group_id        = each.value
  source_security_group_id = aws_security_group.node.id
  from_port                = var.redis_port
  to_port                  = var.redis_port
  protocol                 = "tcp"
  description              = "RIOTX Workbench access"
}
resource "aws_lb" "app" {
  name               = var.name
  internal           = var.alb_internal
  load_balancer_type = "application"
  subnets            = var.alb_subnet_ids
  security_groups    = [aws_security_group.alb.id]
  lifecycle {
    precondition {
      condition     = data.aws_subnet.node.vpc_id == var.vpc_id && alltrue([for s in data.aws_subnet.alb : s.vpc_id == var.vpc_id]) && length(toset([for s in data.aws_subnet.alb : s.availability_zone])) >= 2
      error_message = "All subnets must belong to vpc_id; ALB subnets must span two AZs."
    }
  }
}
resource "aws_lb_target_group" "app" {
  name                 = var.name
  port                 = 8088
  protocol             = "HTTP"
  target_type          = "instance"
  vpc_id               = var.vpc_id
  deregistration_delay = 30
  health_check {
    path                = "/api/health"
    matcher             = "200"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.app.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}
resource "aws_iam_role" "node" {
  name_prefix        = "${var.name}-node-"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy_attachment" "node_ecs" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}
resource "aws_iam_role_policy_attachment" "node_ssm" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_instance_profile" "node" { role = aws_iam_role.node.name }
resource "aws_ebs_volume" "data" {
  availability_zone = data.aws_subnet.node.availability_zone
  size              = var.data_size_gib
  type              = "gp3"
  encrypted         = true
  tags              = { Name = "${var.name}-data" }
  lifecycle { prevent_destroy = true }
}
resource "aws_instance" "node" {
  ami                         = coalesce(var.ami_id, data.aws_ssm_parameter.ami.value)
  instance_type               = var.instance_type
  subnet_id                   = var.instance_subnet_id
  associate_public_ip_address = false
  vpc_security_group_ids      = [aws_security_group.node.id]
  iam_instance_profile        = aws_iam_instance_profile.node.name
  user_data_replace_on_change = true
  user_data = templatefile("${path.module}/user-data.sh.tftpl", {
    cluster   = aws_ecs_cluster.app.name
    volume_id = aws_ebs_volume.data.id
    name      = var.name
  })
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  root_block_device {
    volume_size = 30
    volume_type = "gp3"
    encrypted   = true
  }
  tags = { Name = "${var.name}-ecs-node" }
  lifecycle {
    # Application updates must not silently replace the host when the SSM AMI changes.
    ignore_changes = [ami]
    precondition {
      condition     = contains(data.aws_ec2_instance_type.node.supported_architectures, var.architecture == "ARM64" ? "arm64" : "x86_64")
      error_message = "instance_type must match the image architecture."
    }
    precondition {
      condition     = contains(["required", "supported"], data.aws_ec2_instance_type.node.ebs_nvme_support)
      error_message = "Use a Nitro instance with NVMe EBS support for serial-based persistent disk mounting."
    }
    precondition {
      condition     = data.aws_ec2_instance_type.node.memory_size >= var.task_memory + 1024 && data.aws_ec2_instance_type.node.default_vcpus * 1024 >= var.task_cpu
      error_message = "Reserve at least 1 GiB host memory in addition to task memory; CPU must fit the instance."
    }
  }
  depends_on = [aws_iam_role_policy_attachment.node_ecs, aws_iam_role_policy_attachment.node_ssm]
}
resource "aws_volume_attachment" "data" {
  device_name                    = "/dev/sdf"
  instance_id                    = aws_instance.node.id
  volume_id                      = aws_ebs_volume.data.id
  stop_instance_before_detaching = true
}
resource "aws_iam_role" "execution" {
  name_prefix        = "${var.name}-exec-"
  assume_role_policy = local.trust
}
resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}
resource "aws_iam_role_policy" "secret" {
  role = aws_iam_role.execution.id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = var.token_secret_arn }
    ], var.secret_kms_key_arn == null ? [] : [
    { Effect = "Allow", Action = ["kms:Decrypt"], Resource = var.secret_kms_key_arn }
  ]) })
}
resource "aws_iam_role" "task" {
  name_prefix        = "${var.name}-task-"
  assume_role_policy = local.trust
}
resource "aws_ecs_task_definition" "app" {
  family                   = var.name
  network_mode             = "host"
  requires_compatibilities = ["EC2"]
  cpu                      = tostring(var.task_cpu)
  memory                   = tostring(var.task_memory)
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.architecture
  }
  volume {
    name      = "workbench-data"
    host_path = "/var/lib/riotx-data"
  }
  container_definitions = jsonencode([{
    name            = "workbench", image = var.container_image, essential = true,
    user            = "10001:10001", cpu = var.task_cpu, memory = var.task_memory, stopTimeout = 120,
    portMappings    = [{ containerPort = 8088, hostPort = 8088, protocol = "tcp" }],
    mountPoints     = [{ sourceVolume = "workbench-data", containerPath = "/data", readOnly = false }],
    linuxParameters = { initProcessEnabled = true },
    environment = [
      { name = "DATA_DIR", value = "/data" },
      { name = "DEMO_ENABLED", value = "false" },
      { name = "MAX_CONCURRENT_RUNS", value = tostring(var.max_concurrent_runs) }
    ],
    secrets = [{ name = "WORKBENCH_TOKEN", valueFrom = var.token_secret_arn }],
    healthCheck = {
      command  = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/api/health', timeout=3)\""],
      interval = 30, timeout = 5, retries = 3, startPeriod = 60
    },
    logConfiguration = { logDriver = "awslogs", options = {
      "awslogs-group" = aws_cloudwatch_log_group.app.name, "awslogs-region" = var.region, "awslogs-stream-prefix" = "workbench"
    } }
  }])
}
resource "aws_ecs_service" "app" {
  name                               = var.name
  cluster                            = aws_ecs_cluster.app.id
  task_definition                    = aws_ecs_task_definition.app.arn
  launch_type                        = "EC2"
  desired_count                      = 1
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  health_check_grace_period_seconds  = 120
  wait_for_steady_state              = true
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "workbench"
    container_port   = 8088
  }
  placement_constraints {
    type       = "memberOf"
    expression = "attribute:workbench_node == ${var.name}"
  }
  ordered_placement_strategy {
    type  = "binpack"
    field = "memory"
  }
  depends_on = [aws_lb_listener.https, aws_volume_attachment.data, aws_iam_role_policy_attachment.execution, aws_iam_role_policy.secret]
  timeouts {
    create = "30m"
    update = "30m"
  }
}
