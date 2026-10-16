output "public_ip" {
  value = aws_eip.shared_remote.public_ip
}

output "hostname" {
  value = var.hostname
}

output "instance_id" {
  value = aws_instance.shared_remote.id
}

output "broker_repository_url" {
  value = aws_ecr_repository.images["broker"].repository_url
}

output "edge_repository_url" {
  value = aws_ecr_repository.images["edge"].repository_url
}

output "deploy_role_arn" {
  value = aws_iam_role.deploy.arn
}

output "remote_service_url" {
  value = "https://${var.hostname}"
}
