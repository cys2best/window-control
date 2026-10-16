data "aws_availability_zones" "available" {
  state = "available"
}

data "cloudflare_ip_ranges" "cloudflare" {}

resource "aws_vpc" "shared_remote" {
  cidr_block           = "10.42.0.0/24"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "shared-remote" }
}

resource "aws_internet_gateway" "shared_remote" {
  vpc_id = aws_vpc.shared_remote.id
  tags   = { Name = "shared-remote" }
}

resource "aws_subnet" "public" {
  vpc_id            = aws_vpc.shared_remote.id
  cidr_block        = "10.42.0.0/26"
  availability_zone = data.aws_availability_zones.available.names[0]
  tags              = { Name = "shared-remote-public" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.shared_remote.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.shared_remote.id
  }
  tags = { Name = "shared-remote-public" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# The origin answers only Cloudflare; nothing else is reachable inbound (no SSH: SSM only).
resource "aws_security_group" "shared_remote" {
  name        = "shared-remote"
  description = "HTTPS from Cloudflare only"
  vpc_id      = aws_vpc.shared_remote.id

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
