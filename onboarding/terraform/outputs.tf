output "readonly_role_arn" {
  description = "Value for AWS_ROLE_ARN in the wasteless .env"
  value       = aws_iam_role.readonly.arn
}

output "remediation_role_arn" {
  description = "Value for AWS_WRITE_ROLE_ARN in the wasteless .env (null if not created)"
  value       = var.create_remediation_role ? aws_iam_role.remediation[0].arn : null
}
