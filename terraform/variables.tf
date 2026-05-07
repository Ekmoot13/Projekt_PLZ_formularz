variable "db_password" {
  description = "Haslo do bazy danych"
  type        = string
  sensitive   = true # Terraform ukryje to hasło w logach
}

variable "external_port" {
  description = "Port zewnetrzny dla bazy"
  type        = number
  default     = 5433 # Zmieniamy na 5433, aby uniknąć konfliktu
}