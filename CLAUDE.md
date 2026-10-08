# sp-bus-eta — notes for Claude

Portfolio data project: real bus ride times between two zones in São Paulo.
AWS (EventBridge Scheduler → Lambda → S3, Terraform in `infra/`) feeds a
Databricks Asset Bundle in `pipeline/` (sync_raw → bronze → silver → gold).
The README explains the design; keep new decisions explained there too.

## Rules

- **Public repo.** The home zone never goes into code, config, commits,
  tables or dashboards: it lives in `config/places.local.json` (git-ignored)
  and in the Databricks secret scope `sp-bus-eta`. Only the park zone is
  public.
- Everything runs in the cloud. Nothing goes on the Raspberry Pi.
- Scope is deliberately small: direct lines only, two circular zones, ride
  time only. Walking, waiting at the stop, GTFS and exact stops are out of
  scope; don't reintroduce them.
- Raw is the contract: the collector stores API responses untouched. All
  cleaning happens downstream.
- Work goes through a branch + PR. Merge only when Thomaz asks.

## Commands

```bash
# Infra (from infra/; region sa-east-1, state in S3 with use_lockfile)
terraform init
terraform plan "-out=plan.tfplan"   # quote it: PowerShell 5.1 breaks -out=...
terraform apply plan.tfplan

# Databricks (from pipeline/; CLI profile DEFAULT)
databricks bundle validate
databricks bundle deploy
databricks bundle run pipeline
```

To query tables, submit a one-off job that prints the result
(`databricks jobs submit`). `databricks api post /api/2.0/sql/statements`
returns Not Found on this workspace.

## Pitfalls already solved

- The Olho Vivo API returns 403 to Python's default User-Agent: send a custom one.
- `spark_python_task` runs under IPython, so `sys.exit(0)` marks the task
  FAILED. Call `main()` and return.
- `databricks.exe` is a Windows binary and can't see Git Bash's `/tmp`.
- The SPTrans token is an SSM SecureString created outside Terraform; keep it
  out of state. Rotate the Databricks read-only AWS key with
  `scripts/rotate_databricks_key.py`.
- Scheduler trust policy: `aws:SourceArn` is the schedule group's ARN.
- The AWS MCP `run_script` blocks `gzip`; read raw files with local boto3.
- Schema `workspace.bronze` predates this project and is not ours.
