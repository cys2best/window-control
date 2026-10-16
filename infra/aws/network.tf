data "aws_availability_zones" "available" {
  state = "available"
}

data "cloudflare_ip_ranges" "cloudflare" {}

# Default VPC already has an internet gateway and a public default subnet per AZ.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnet" "default" {
  vpc_id            = data.aws_vpc.default.id
  availability_zone = data.aws_availability_zones.available.names[0]
  default_for_az    = true
}

# The origin answers only Cloudflare; nothing else is reachable inbound (no SSH: SSM only).
resource "aws_security_group" "shared_remote" {
  name        = "shared-remote"
  description = "HTTPS from Cloudflare only"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTPS from Cloudflare proxy"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = data.cloudflare_ip_ranges.cloudflare.ipv4_cidrs
  }

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
