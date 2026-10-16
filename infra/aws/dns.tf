data "cloudflare_zone" "zone" {
  filter = {
    name = var.cloudflare_zone
  }
}

resource "cloudflare_dns_record" "shared_remote" {
  zone_id = data.cloudflare_zone.zone.zone_id
  name    = var.hostname
  type    = "A"
  content = aws_eip.shared_remote.public_ip
  proxied = true
  ttl     = 1
}

# Full (strict): Cloudflare verifies the origin's Origin CA certificate.
resource "cloudflare_zone_setting" "ssl" {
  zone_id    = data.cloudflare_zone.zone.zone_id
  setting_id = "ssl"
  value      = "strict"
}

resource "tls_private_key" "origin" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_cert_request" "origin" {
  private_key_pem = tls_private_key.origin.private_key_pem
  subject {
    common_name = var.hostname
  }
}

resource "cloudflare_origin_ca_certificate" "origin" {
  csr                = tls_cert_request.origin.cert_request_pem
  hostnames          = [var.hostname]
  request_type       = "origin-rsa"
  requested_validity = 5475
}
