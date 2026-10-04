terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }

  # The state bucket is created once by hand (see README): it cannot be
  # managed by the same state it stores. S3-native locking, no DynamoDB table.
  backend "s3" {
    bucket       = "sp-bus-eta-tfstate-669958786709"
    key          = "sp-bus-eta/terraform.tfstate"
    region       = "sa-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
      Repo      = "github.com/thomazcampos07/sp-bus-eta"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  # Built by hand instead of read with a data source, so the token value never
  # lands in the Terraform state. The parameter itself is created with the CLI.
  token_parameter_arn = "arn:aws:ssm:${var.region}:${local.account_id}:parameter${var.token_parameter_name}"
}
