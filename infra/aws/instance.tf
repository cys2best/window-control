data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_instance" "shared_remote" {
  ami                    = data.aws_ssm_parameter.al2023_arm64.value
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.shared_remote.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name
  user_data = templatefile("${path.module}/user-data.sh.tftpl", {
    data_volume_id = replace(aws_ebs_volume.data.id, "-", "")
  })

  credit_specification {
    cpu_credits = "standard"
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "enabled"
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 8
    encrypted   = true
  }

  tags = {
    Name                 = "shared-remote"
    "max-active-streams" = tostring(var.max_active_streams)
    "remote-hostname"    = var.hostname
  }

  lifecycle {
    ignore_changes = [user_data, ami]
  }
}

resource "aws_eip" "shared_remote" {
  domain   = "vpc"
  instance = aws_instance.shared_remote.id
  tags     = { Name = "shared-remote" }
}
