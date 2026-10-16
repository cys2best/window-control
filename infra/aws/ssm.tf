# Terraform owns only the origin certificate pair. The Cloudflare TURN key ID and API token
# (/window-control/shared-remote/cloudflare-turn-*) are put by the owner and never enter state.
resource "aws_ssm_parameter" "origin_cert" {
  name  = "/window-control/shared-remote/origin-cert"
  type  = "SecureString"
  value = cloudflare_origin_ca_certificate.origin.certificate
}

resource "aws_ssm_parameter" "origin_key" {
  name  = "/window-control/shared-remote/origin-key"
  type  = "SecureString"
  value = tls_private_key.origin.private_key_pem
}
