# infra/terraform/variables.tf
variable "aws_region" {
  description = "AWS region for the VPS"
  type        = string
  default     = "ap-southeast-1"
}

variable "instance_type" {
  description = "EC2 instance type for coturn + signaling server"
  type        = string
  default     = "t3.small"
}

variable "key_pair_name" {
  description = "Name of an existing EC2 key pair for SSH access"
  type        = string
}

variable "ssh_allowed_cidr" {
  description = "CIDR block allowed to SSH into the instance (restrict to your IP in production)"
  type        = string
  default     = "0.0.0.0/0"
}
