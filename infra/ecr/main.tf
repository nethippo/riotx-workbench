terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}
provider "aws" {
  region = var.region
  default_tags { tags = { Project = "riotx-workbench", ManagedBy = "terraform" } }
}
variable "region" {
  type    = string
  default = "ap-northeast-2"
}
variable "repository_name" {
  type    = string
  default = "riotx-workbench"
}
resource "aws_ecr_repository" "app" {
  name                 = var.repository_name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
}
output "repository_url" { value = aws_ecr_repository.app.repository_url }
output "region" { value = var.region }
