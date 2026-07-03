output "expected_detections" {
  description = "Resource IDs each detector is expected to flag"
  value = {
    eip_orphan          = aws_eip.orphan.allocation_id
    nat_gateway_unused  = aws_nat_gateway.unused.id
    ebs_gp2_migration   = aws_ebs_volume.gp2.id
    elb_unused          = aws_lb.unused.arn
  }
}
