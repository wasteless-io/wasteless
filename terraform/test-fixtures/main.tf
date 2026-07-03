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
