variable "project" {
  description = "Name used as a prefix for every resource."
  type        = string
  default     = "sp-bus-eta"
}

variable "region" {
  description = "AWS region. Sao Paulo keeps the collector next to the data source."
  type        = string
  default     = "sa-east-1"
}

variable "token_parameter_name" {
  description = "SSM SecureString holding the SPTrans Olho Vivo token."
  type        = string
  default     = "/sp-bus-eta/sptrans-token"
}

variable "raw_prefix" {
  description = "S3 prefix where the collector lands raw API responses."
  type        = string
  default     = "raw/positions"
}

variable "schedule_expression" {
  description = "How often the collector runs."
  type        = string
  default     = "rate(1 minute)"
}

variable "hc_ping_url" {
  description = "Optional Healthchecks.io ping URL (dead man's switch). Empty disables it."
  type        = string
  default     = ""
  sensitive   = true
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the collector."
  type        = number
  default     = 14
}
