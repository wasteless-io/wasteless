variable "trusted_principal_arns" {
  description = "IAM principal ARNs allowed to assume the wasteless roles (the identity wasteless runs with)."
  type        = list(string)

  validation {
    condition     = length(var.trusted_principal_arns) > 0
    error_message = "Provide at least one trusted principal ARN."
  }
}

variable "external_id" {
  description = "Optional ExternalId the caller must present in sts:AssumeRole. Recommended for cross-account trust."
  type        = string
  default     = ""
}

variable "role_name_prefix" {
  description = "Prefix for the created role names (<prefix>-readonly, <prefix>-remediation)."
  type        = string
  default     = "wasteless"
}

variable "create_remediation_role" {
  description = "Create the write role. Set to false for detection-only onboarding."
  type        = bool
  default     = true
}
