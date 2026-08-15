# infra/terraform/outputs.tf
output "instance_public_ip" {
  description = "Public IP of the VPS. Use this for Task 1's <VPS_PUBLIC_IP> substitution and Task 4's deploy steps."
  value       = aws_instance.webrtc_poc.public_ip
}

output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.webrtc_poc.id
}

output "ssh_command" {
  description = "SSH command to connect to the instance"
  value       = "ssh -i <path-to-key_pair_name>.pem ubuntu@${aws_instance.webrtc_poc.public_ip}"
}
