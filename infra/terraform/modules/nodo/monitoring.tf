# Observability and alerting (ADR-018). Every service writes diagnostics to the workspace; the
# application writes traces, metrics and logs with W3C context; alerts follow the service level
# objectives (symptoms people feel) first and resource saturation (causes) second.

locals {
  error_budget = (100 - var.slo_availability_target) / 100
  public_host  = local.pro ? (var.custom_domain != null ? var.custom_domain : azurerm_cdn_frontdoor_endpoint.main[0].host_name) : ""

  diagnostic_targets = local.pro ? merge(
    {
      keyvault   = azurerm_key_vault.main.id
      registry   = azurerm_container_registry.main.id
      audio      = "${azurerm_storage_account.main.id}/blobServices/default"
      audit      = "${azurerm_storage_account.audit[0].id}/blobServices/default"
      servicebus = azurerm_servicebus_namespace.main.id
      postgres   = azurerm_postgresql_flexible_server.main.id
      search     = azurerm_search_service.main.id
      openai     = azurerm_cognitive_account.openai.id
      speech     = azurerm_cognitive_account.speech.id
      gateway    = azurerm_api_management.main[0].id
      edge       = azurerm_cdn_frontdoor_profile.main[0].id
    },
    local.dr ? {
      replica   = azurerm_postgresql_flexible_server.replica[0].id
      openai_dr = azurerm_cognitive_account.openai_dr[0].id
    } : {},
  ) : {}
}

resource "azurerm_monitor_diagnostic_setting" "main" {
  for_each                   = local.diagnostic_targets
  name                       = "to-workspace"
  target_resource_id         = each.value
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id

  enabled_log {
    category_group = "allLogs"
  }

  enabled_metric {
    category = "AllMetrics"
  }
}

resource "azurerm_monitor_action_group" "oncall" {
  count               = local.pro ? 1 : 0
  name                = "ag-${local.name}-oncall"
  resource_group_name = azurerm_resource_group.main.name
  short_name          = "nodo"
  tags                = local.tags

  dynamic "email_receiver" {
    for_each = toset(var.alert_emails)
    content {
      name                    = "mail-${substr(sha1(email_receiver.value), 0, 8)}"
      email_address           = email_receiver.value
      use_common_alert_schema = true
    }
  }
}

# --- is it up? --------------------------------------------------------------------------------
# A synthetic user from five places, through the same door real users use.

resource "azurerm_application_insights_standard_web_test" "ready" {
  count                   = local.pro ? 1 : 0
  name                    = "wt-${local.name}-ready"
  resource_group_name     = azurerm_resource_group.main.name
  location                = azurerm_resource_group.main.location
  application_insights_id = azurerm_application_insights.main.id
  geo_locations           = ["emea-nl-ams-azr", "emea-gb-db3-azr", "emea-fr-pra-edge", "emea-se-sto-edge", "apac-sg-sin-azr"]
  frequency               = 300
  timeout                 = 30
  retry_enabled           = true
  tags                    = merge(local.tags, { "hidden-link:${azurerm_application_insights.main.id}" = "Resource" })

  request {
    url = "https://${local.public_host}/readyz"
  }

  validation_rules {
    expected_status_code = 200
    ssl_check_enabled    = true
  }
}

resource "azurerm_monitor_metric_alert" "availability" {
  count               = local.pro ? 1 : 0
  name                = "alert-${local.name}-availability"
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [azurerm_application_insights.main.id, azurerm_application_insights_standard_web_test.ready[0].id]
  description         = "The readiness check fails from two or more places: users cannot use the service."
  severity            = 0
  frequency           = "PT1M"
  window_size         = "PT5M"
  tags                = local.tags

  application_insights_web_test_location_availability_criteria {
    web_test_id           = azurerm_application_insights_standard_web_test.ready[0].id
    component_id          = azurerm_application_insights.main.id
    failed_location_count = 2
  }

  action {
    action_group_id = azurerm_monitor_action_group.oncall[0].id
  }
}

# --- are we keeping the promise? --------------------------------------------------------------
# Multi-window burn rate on the error budget: fast burn pages (14 times the sustainable rate would
# spend a month's budget in two days), slow burn opens a ticket-level alert.

locals {
  request_filter = "AppRequests | where Name !has '/healthz' and Name !has '/readyz'"

  burn_rates = {
    # Long window decides that the budget is burning; the short one (a twelfth of it) proves it still is,
    # so the alert clears soon after the problem does.
    fast = { window = "PT1H", every = "PT5M", burn = 14.4, severity = 1, long = "1h", short = "5m" }
    slow = { window = "PT6H", every = "PT30M", burn = 6, severity = 2, long = "6h", short = "30m" }
  }
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "burn" {
  for_each              = local.pro ? local.burn_rates : {}
  name                  = "alert-${local.name}-burn-${each.key}"
  resource_group_name   = azurerm_resource_group.main.name
  location              = azurerm_resource_group.main.location
  scopes                = [azurerm_log_analytics_workspace.main.id]
  description           = "The error budget of the API is burning ${each.value.burn} times faster than the objective of ${var.slo_availability_target}% allows."
  display_name          = "SLO burn rate (${each.key})"
  severity              = each.value.severity
  evaluation_frequency  = each.value.every
  window_duration       = each.value.window
  skip_query_validation = true
  tags                  = local.tags

  criteria {
    query = <<-KQL
      ${local.request_filter}
      | where TimeGenerated > ago(${each.value.long})
      | extend failed = (Success == false and toint(ResultCode) >= 500), recent = TimeGenerated > ago(${each.value.short})
      | summarize total = count(), failures = countif(failed), recent_total = countif(recent), recent_failures = countif(recent and failed)
      | extend ratio = iff(total == 0, 0.0, todouble(failures) / total), recent_ratio = iff(recent_total == 0, 0.0, todouble(recent_failures) / recent_total)
      | where total > 50 and ratio > ${local.error_budget * each.value.burn} and recent_ratio > ${local.error_budget * each.value.burn}
    KQL

    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 0

    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }

  action {
    action_groups = [azurerm_monitor_action_group.oncall[0].id]
  }
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "latency" {
  count                 = local.pro ? 1 : 0
  name                  = "alert-${local.name}-latency"
  resource_group_name   = azurerm_resource_group.main.name
  location              = azurerm_resource_group.main.location
  scopes                = [azurerm_log_analytics_workspace.main.id]
  description           = "The 95th percentile of read requests is above ${var.slo_latency_p95_ms} ms."
  display_name          = "Read latency above objective"
  severity              = 2
  evaluation_frequency  = "PT5M"
  window_duration       = "PT15M"
  skip_query_validation = true
  tags                  = local.tags

  criteria {
    query = <<-KQL
      ${local.request_filter}
      | where TimeGenerated > ago(15m) and Name startswith 'GET '
      | summarize n = count(), p95 = percentile(DurationMs, 95)
      | where n > 30 and p95 > ${var.slo_latency_p95_ms}
    KQL

    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 0

    failing_periods {
      minimum_failing_periods_to_trigger_alert = 2
      number_of_evaluation_periods             = 3
    }
  }

  action {
    action_groups = [azurerm_monitor_action_group.oncall[0].id]
  }
}

# Jobs write one summary line; a failure count above zero is a meeting whose audio was not erased.
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "retention" {
  count                 = local.pro ? 1 : 0
  name                  = "alert-${local.name}-retention"
  resource_group_name   = azurerm_resource_group.main.name
  location              = azurerm_resource_group.main.location
  scopes                = [azurerm_log_analytics_workspace.main.id]
  description           = "The retention job could not erase the audio of at least one meeting. This is a compliance event."
  display_name          = "Audio retention failed"
  severity              = 1
  evaluation_frequency  = "PT30M"
  window_duration       = "PT1H"
  skip_query_validation = true
  tags                  = local.tags

  criteria {
    query = <<-KQL
      ContainerAppConsoleLogs_CL
      | where TimeGenerated > ago(1h)
      | where Log_s has 'retention erased=' and Log_s matches regex 'failed=[1-9]'
    KQL

    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 0

    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }

  action {
    action_groups = [azurerm_monitor_action_group.oncall[0].id]
  }
}

# The weekly verify logs one summary line and exits 1 when the index or the graph differs from the
# database; a reindex that could not repair a meeting does the same.
resource "azurerm_monitor_scheduled_query_rules_alert_v2" "index" {
  count                 = local.pro ? 1 : 0
  name                  = "alert-${local.name}-index"
  resource_group_name   = azurerm_resource_group.main.name
  location              = azurerm_resource_group.main.location
  scopes                = [azurerm_log_analytics_workspace.main.id]
  description           = "The search index or the graph differs from the database: questions may miss recordings. Run the reindex job (RUNBOOKS.md section 5)."
  display_name          = "Search index out of step with the database"
  severity              = 2
  evaluation_frequency  = "PT1H"
  window_duration       = "P1D"
  skip_query_validation = true
  tags                  = local.tags

  criteria {
    query = <<-KQL
      ContainerAppConsoleLogs_CL
      | where TimeGenerated > ago(1d)
      | where Log_s has 'index_verify' or Log_s has 'index_repair'
      | where Log_s matches regex '(findings|failed)=[1-9]'
    KQL

    time_aggregation_method = "Count"
    operator                = "GreaterThan"
    threshold               = 0

    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }

  action {
    action_groups = [azurerm_monitor_action_group.oncall[0].id]
  }
}

# --- are we running out of something? ---------------------------------------------------------

locals {
  metric_alerts = local.pro ? merge(
    {
      postgres-cpu = {
        scope       = azurerm_postgresql_flexible_server.main.id, metric = "cpu_percent", namespace = "Microsoft.DBforPostgreSQL/flexibleServers"
        aggregation = "Average", operator = "GreaterThan", threshold = 80, window = "PT15M", severity = 2
        description = "PostgreSQL CPU above 80% for 15 minutes."
      }
      postgres-storage = {
        scope       = azurerm_postgresql_flexible_server.main.id, metric = "storage_percent", namespace = "Microsoft.DBforPostgreSQL/flexibleServers"
        aggregation = "Maximum", operator = "GreaterThan", threshold = 85, window = "PT30M", severity = 2
        description = "PostgreSQL storage above 85%."
      }
      queue-dead-letters = {
        scope       = azurerm_servicebus_namespace.main.id, metric = "DeadletteredMessages", namespace = "Microsoft.ServiceBus/namespaces"
        aggregation = "Maximum", operator = "GreaterThan", threshold = 0, window = "PT5M", severity = 2
        description = "A message ended in the dead-letter queue: a recording that could not be processed."
      }
      queue-backlog = {
        scope       = azurerm_servicebus_namespace.main.id, metric = "ActiveMessages", namespace = "Microsoft.ServiceBus/namespaces"
        aggregation = "Average", operator = "GreaterThan", threshold = 200, window = "PT15M", severity = 3
        description = "More than 200 recordings waiting for 15 minutes: workers are not keeping up."
      }
      edge-origin-health = {
        scope       = azurerm_cdn_frontdoor_profile.main[0].id, metric = "OriginHealthPercentage", namespace = "Microsoft.Cdn/profiles"
        aggregation = "Average", operator = "LessThan", threshold = 50, window = "PT5M", severity = 1
        description = "Front Door sees less than half of its origins healthy: a region is failing or the gateway is down."
      }
      gateway-capacity = {
        scope       = azurerm_api_management.main[0].id, metric = "Capacity", namespace = "Microsoft.ApiManagement/service"
        aggregation = "Average", operator = "GreaterThan", threshold = 70, window = "PT15M", severity = 3
        description = "The gateway is above 70% of its capacity: add a unit before it becomes the limit."
      }
    },
    local.dr ? {
      replica-lag = {
        scope       = azurerm_postgresql_flexible_server.replica[0].id, metric = "physical_replication_delay_in_seconds", namespace = "Microsoft.DBforPostgreSQL/flexibleServers"
        aggregation = "Maximum", operator = "GreaterThan", threshold = 60, window = "PT15M", severity = 2
        description = "The standby database is more than a minute behind: the recovery point objective is at risk."
      }
    } : {},
  ) : {}
}

resource "azurerm_monitor_metric_alert" "resource" {
  for_each            = local.metric_alerts
  name                = "alert-${local.name}-${each.key}"
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [each.value.scope]
  description         = each.value.description
  severity            = each.value.severity
  frequency           = "PT5M"
  window_size         = each.value.window
  tags                = local.tags

  criteria {
    metric_namespace = each.value.namespace
    metric_name      = each.value.metric
    aggregation      = each.value.aggregation
    operator         = each.value.operator
    threshold        = each.value.threshold
  }

  action {
    action_group_id = azurerm_monitor_action_group.oncall[0].id
  }
}

# --- one page to look at ----------------------------------------------------------------------

resource "random_uuid" "workbook" {}

resource "azurerm_application_insights_workbook" "overview" {
  count               = local.pro ? 1 : 0
  name                = random_uuid.workbook.result
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  display_name        = "NODO AI overview"
  source_id           = lower(azurerm_log_analytics_workspace.main.id)
  tags                = local.tags

  data_json = jsonencode({
    version = "Notebook/1.0"
    items = [
      {
        type    = 1
        content = { json = "## NODO AI\nObjective: ${var.slo_availability_target}% of requests without a server error over 30 days, read p95 under ${var.slo_latency_p95_ms} ms. Dashboards show symptoms first; causes follow." }
      },
      {
        type = 3
        content = {
          version       = "KqlItem/1.0"
          title         = "Requests and server errors, per 5 minutes"
          queryType     = 0
          resourceType  = "microsoft.operationalinsights/workspaces"
          visualization = "timechart"
          size          = 1
          query         = "${local.request_filter} | summarize requests = count(), errors = countif(Success == false and toint(ResultCode) >= 500) by bin(TimeGenerated, 5m)"
        }
      },
      {
        type = 3
        content = {
          version       = "KqlItem/1.0"
          title         = "Latency, 50th and 95th percentile"
          queryType     = 0
          resourceType  = "microsoft.operationalinsights/workspaces"
          visualization = "timechart"
          size          = 1
          query         = "${local.request_filter} | summarize p50 = percentile(DurationMs, 50), p95 = percentile(DurationMs, 95) by bin(TimeGenerated, 5m)"
        }
      },
      {
        type = 3
        content = {
          version       = "KqlItem/1.0"
          title         = "Error budget left this month"
          queryType     = 0
          resourceType  = "microsoft.operationalinsights/workspaces"
          visualization = "table"
          size          = 4
          query         = "${local.request_filter} | where TimeGenerated > startofmonth(now()) | summarize total = count(), failed = countif(Success == false and toint(ResultCode) >= 500) | extend budget_left_percent = round(100.0 * (1.0 - (todouble(failed) / total) / ${local.error_budget}), 1)"
        }
      },
      {
        type = 3
        content = {
          version       = "KqlItem/1.0"
          title         = "Requests blocked by the firewall, by rule"
          queryType     = 0
          resourceType  = "microsoft.operationalinsights/workspaces"
          visualization = "barchart"
          size          = 1
          query         = "AzureDiagnostics | where ResourceType == 'FRONTDOORS' or Category == 'FrontDoorWebApplicationFirewallLog' | where action_s == 'Block' | summarize blocked = count() by ruleName_s | top 10 by blocked"
        }
      },
    ]
  })
}
