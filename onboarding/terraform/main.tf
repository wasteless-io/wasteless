# Wasteless onboarding roles.
# Permission policies are read verbatim from onboarding/policies/*.json,
# the single source of truth shared with the CloudFormation template.

data "aws_iam_policy_document" "trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = var.trusted_principal_arns
    }

    dynamic "condition" {
      for_each = var.external_id != "" ? [1] : []
      content {
        test     = "StringEquals"
        variable = "sts:ExternalId"
        values   = [var.external_id]
      }
    }
  }
}

resource "aws_iam_role" "readonly" {
  name                 = "${var.role_name_prefix}-readonly"
  description          = "Read-only analysis role for wasteless (cloud cost waste detection). Grants only Describe/Get/List calls on resource metadata, CloudWatch metrics and Cost Explorer: wasteless can see which resources exist and how much they are used, but can never read the data they contain, never create, modify or delete anything, and never touch IAM. Sessions last 1 hour max; delete this role to revoke all access instantly."
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600

  tags = {
    Application = "wasteless"
  }
}

resource "aws_iam_role_policy" "readonly" {
  name   = "wasteless-readonly"
  role   = aws_iam_role.readonly.id
  policy = file("${path.module}/../policies/readonly.json")
}

resource "aws_iam_role" "remediation" {
  count = var.create_remediation_role ? 1 : 0

  name                 = "${var.role_name_prefix}-remediation"
  description          = "Remediation role for wasteless, assumed only to execute waste cleanup actions approved by a human in the wasteless UI (stop an idle instance, delete an orphaned volume, ...). Limited to the listed EC2/ELB cleanup actions: no IAM access, and the only thing it can create is a rollback snapshot taken before destructive EBS actions. Sessions last 1 hour max; delete this role to revoke all access instantly (wasteless then falls back to detection mode)."
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600

  tags = {
    Application = "wasteless"
  }
}

# Remediators describe resources before acting: attach read too
resource "aws_iam_role_policy" "remediation_readonly" {
  count = var.create_remediation_role ? 1 : 0

  name   = "wasteless-readonly"
  role   = aws_iam_role.remediation[0].id
  policy = file("${path.module}/../policies/readonly.json")
}

resource "aws_iam_role_policy" "remediation" {
  count = var.create_remediation_role ? 1 : 0

  name   = "wasteless-remediation"
  role   = aws_iam_role.remediation[0].id
  policy = file("${path.module}/../policies/remediation.json")
}
