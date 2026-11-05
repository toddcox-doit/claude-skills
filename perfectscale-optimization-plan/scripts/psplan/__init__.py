"""psplan - PerfectScale tenant optimization plan library.

Pipeline: collect (pscli, read-only) -> model (normalize + classify) -> actions (rule engine)
-> render (md/html/xlsx) -> verify (independent recompute). Tenant-agnostic: nothing in here
names a customer; everything comes from the config file and the collected data.
"""
__version__ = "2.0.0"
