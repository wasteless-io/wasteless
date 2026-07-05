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
