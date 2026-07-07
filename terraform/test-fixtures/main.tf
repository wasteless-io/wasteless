terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      "wasteless"   = "test-fixture"
      "environment" = "test"
      "managed-by"  = "terraform"
    }
  }
}

variable "region" {
  description = "Region to create fixtures in (must be scanned by the detectors)"
  type        = string
  default     = "eu-west-3"
}

variable "holder_ami" {
  description = "AMI for the gp2-volume holder instance (hardcoded: the wasteless test IAM user lacks ssm:GetParameter/ec2:DescribeImages, so no dynamic lookup)"
  type        = string
  default     = "ami-09d87c5f7372e202a" # al2023-ami-2023.12.20260629.0-kernel-6.1-x86_64, eu-west-3
}
