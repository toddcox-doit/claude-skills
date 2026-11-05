# Tenant config keys
See `psplan/config.py` DEFAULTS for every key and default. Required: customer, clusters (name + uid). Paths are relative to the folder containing `config/`. `thresholds`, `stateful_patterns`, `nonprod_patterns`, `managed_namespaces`, `auto_provisioned_pool_patterns`, `gpu_pool_patterns` tune classification without code changes.

`foreign_tenants` is the leak-scan list: the names of other customers this working copy has been run against. The verifier fails the run if one of them reaches a rendered file. It defaults to empty, so set it on every run in a folder that has held more than one tenant. No customer name is hard-coded in the library.
