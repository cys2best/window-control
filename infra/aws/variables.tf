variable "hostname" {
  description = "Public name of the remote service, proxied by Cloudflare."
  type        = string
  default     = "emuctrl.nendojo.com"
}

variable "cloudflare_zone" {
  description = "Cloudflare zone that holds the hostname."
  type        = string
  default     = "nendojo.com"
}

variable "instance_type" {
  description = "t4g.nano by default; switch to t4g.micro if the box swaps steadily."
  type        = string
  default     = "t4g.nano"
  validation {
    condition     = contains(["t4g.nano", "t4g.micro"], var.instance_type)
    error_message = "instance_type must be t4g.nano or t4g.micro."
  }
}

variable "budget_email" {
  description = "Recipient of the monthly budget alerts."
  type        = string
  default     = "tuananh.ngo@koeeru.com"
}

variable "github_repository" {
  description = "Repository whose deploy workflow may assume the deploy role."
  type        = string
  default     = "cys2best/window-control"
}

variable "deploy_branches" {
  description = "Branches whose workflow runs may assume the deploy role."
  type        = list(string)
  default     = ["main", "feat/direct-webrtc-shared-turn"]
}

variable "max_active_streams" {
  description = "Broker admission cap; passed to the deploy script through the instance tag."
  type        = number
  default     = 4
}
