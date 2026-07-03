# One billed fixture per detector under validation.
# Estimated total: ~0.08 USD/hour. ALWAYS `terraform destroy` when done.

# --- eip_orphan: unassociated Elastic IP (~0.005 USD/h) ---------------------

resource "aws_eip" "orphan" {
  domain = "vpc"

  tags = { Name = "wasteless-fixture-orphan-eip" }
}

# --- nat_gateway_unused: NAT gateway with zero traffic (~0.048 USD/h) -------
# connectivity_type = "private" needs no EIP; same hourly billing and same
# detection path (no outbound traffic in 30 days).

resource "aws_nat_gateway" "unused" {
  subnet_id         = aws_subnet.a.id
  connectivity_type = "private"

  tags = { Name = "wasteless-fixture-unused-nat" }
}

# --- ebs_gp2_migration: attached gp2 volume (t3.nano ~0.0059 USD/h) ---------
# The gp2 detector only flags volumes in 'in-use' state, so the volume must
# be attached to an instance.

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_instance" "holder" {
  ami           = data.aws_ssm_parameter.al2023.value
  instance_type = "t3.nano"
  subnet_id     = aws_subnet.a.id

  tags = { Name = "wasteless-fixture-gp2-holder" }
}

resource "aws_ebs_volume" "gp2" {
  availability_zone = "${var.region}a"
  size              = 4
  type              = "gp2"

  tags = { Name = "wasteless-fixture-gp2-volume" }
}

resource "aws_volume_attachment" "gp2" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.gp2.id
  instance_id = aws_instance.holder.id
}

# --- elb_unused: internal ALB with no target group (~0.0252 USD/h) ----------

resource "aws_lb" "unused" {
  name               = "wasteless-fixture-unused-alb"
  internal           = true
  load_balancer_type = "application"
  subnets            = [aws_subnet.a.id, aws_subnet.b.id]

  tags = { Name = "wasteless-fixture-unused-alb" }
}
