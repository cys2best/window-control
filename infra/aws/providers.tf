provider "aws" {
  region = "ap-southeast-1"
  default_tags {
    tags = local.tags
  }
}

# Reads CLOUDFLARE_API_TOKEN from the environment; never stored in these files.
provider "cloudflare" {}

locals {
  tags = {
    project   = "window-control"
    component = "shared-remote"
  }
}

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}
