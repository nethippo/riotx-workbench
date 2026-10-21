variable "region" {
  type    = string
  default = "ap-northeast-2"
}
variable "name" {
  type    = string
  default = "riotx-workbench"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,24}$", var.name))
    error_message = "name must be 3-25 lowercase letters, digits or hyphens, starting with a letter."
  }
}
variable "tags" {
  type    = map(string)
  default = {}
}
variable "vpc_id" { type = string }
variable "alb_subnet_ids" {
  type = list(string)
  validation {
    condition     = length(toset(var.alb_subnet_ids)) >= 2
    error_message = "Provide ALB subnets in at least two different availability zones."
  }
}
variable "instance_subnet_id" { type = string }
variable "alb_internal" {
  type    = bool
  default = true
}
variable "allowed_client_cidrs" {
  type = list(string)
  validation {
    condition     = length(var.allowed_client_cidrs) > 0 && alltrue([for c in var.allowed_client_cidrs : can(cidrnetmask(c)) && c != "0.0.0.0/0" && c != "::/0"])
    error_message = "Specify explicit client IPv4 CIDRs; an unrestricted /0 is not supported."
  }
}
variable "certificate_arn" {
  type = string
  validation {
    condition     = can(regex("^arn:[^:]+:acm:", var.certificate_arn))
    error_message = "Provide an issued ACM certificate ARN in this region."
  }
}
variable "token_secret_arn" {
  type = string
  validation {
    condition     = can(regex("^arn:[^:]+:secretsmanager:", var.token_secret_arn))
    error_message = "Provide an existing Secrets Manager ARN containing a plain-text Workbench token."
  }
}
variable "secret_kms_key_arn" {
  type    = string
  default = null
}
variable "container_image" {
  type = string
  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.container_image))
    error_message = "Pin the image by digest (repository@sha256:...), or run scripts/deploy/ecs.sh image."
  }
}
variable "architecture" {
  type    = string
  default = "X86_64"
  validation {
    condition     = contains(["X86_64", "ARM64"], var.architecture)
    error_message = "architecture must be X86_64 or ARM64 and match the image and instance type."
  }
}
variable "instance_type" {
  type    = string
  default = "t3.large"
}
variable "ami_id" {
  type    = string
  default = null
}
variable "data_size_gib" {
  type    = number
  default = 30
  validation {
    condition     = var.data_size_gib >= 10 && floor(var.data_size_gib) == var.data_size_gib
    error_message = "data_size_gib must be an integer of at least 10."
  }
}
variable "task_cpu" {
  type    = number
  default = 1024
  validation {
    condition     = var.task_cpu >= 128 && floor(var.task_cpu) == var.task_cpu
    error_message = "task_cpu must be an integer of at least 128 CPU units."
  }
}
variable "task_memory" {
  type    = number
  default = 4096
  validation {
    condition     = var.task_memory >= 512 && floor(var.task_memory) == var.task_memory
    error_message = "task_memory must be an integer of at least 512 MiB."
  }
}
variable "max_concurrent_runs" {
  type    = number
  default = 1
  validation {
    condition     = var.max_concurrent_runs >= 1 && var.max_concurrent_runs <= 8 && floor(var.max_concurrent_runs) == var.max_concurrent_runs
    error_message = "Use an integer between 1 and 8; provision host/task memory accordingly."
  }
}
variable "redis_security_group_ids" {
  type    = set(string)
  default = []
}
variable "redis_port" {
  type    = number
  default = 6379
}
variable "log_retention_days" {
  type    = number
  default = 30
}
