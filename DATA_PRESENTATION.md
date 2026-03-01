# Athena Audit — Data Presentation Options

## Overview

The `events` table contains enriched audit records with user identity, query text, status, and workgroup information. Data custodians need a way to search, filter, and review this data without requiring SQL expertise.

---

## Option 1: Interactive Jupyter Notebook (Included)

**Best for:** Ad-hoc investigation, data custodians comfortable with a notebook interface

A Jupyter notebook (`notebooks/audit_dashboard.ipynb`) is included in this project. It provides:
- Pre-built queries with interactive filters (date range, user, workgroup, status)
- Summary statistics and charts (queries per user, per workgroup, failure rates)
- Full-text search across query SQL
- Exportable results to CSV

**Requirements:** Python, `boto3`, `pyathena`, `pandas`, `ipywidgets`, `plotly`

**How to run:**
```bash
pip install pyathena pandas ipywidgets plotly
jupyter lab notebooks/audit_dashboard.ipynb
```

**Cost:** Free (just Athena per-query costs ~$5/TB scanned)  
**Effort:** Included — ready to use

---

## Option 2: Amazon QuickSight Dashboard

**Best for:** Non-technical data custodians who need a polished, always-on dashboard

- Connects directly to Athena — no SQL needed
- Pre-built dashboards with drag-and-drop filters: date range, user, workgroup, status
- Search/filter by `source_identity`, drill into query details
- Scheduled email reports (e.g., "daily audit summary")
- Role-based access control

**Cost:** ~$12/user/month (Reader), $18/user/month (Author)  
**Effort:** Low — point it at the `events` table, build 2–3 visuals

**Setup steps:**
1. Open QuickSight → Datasets → New Dataset → Athena
2. Select database `athena_events`, table `events`
3. Build visuals: bar chart (queries by user), table (recent queries), pie chart (status breakdown)
4. Publish and share with data custodian users

---

## Option 3: Athena Views + Saved Queries

**Best for:** Users comfortable with the Athena console query editor

- Create a simplified **view** with business-friendly column names
- Provide saved named queries for common audit scenarios
- Users modify WHERE clauses to filter

**Cost:** Free (just Athena per-query costs)  
**Effort:** Minimal

**Example view:**
```sql
CREATE OR REPLACE VIEW athena_events.audit_report AS
SELECT
  event_time AS "Timestamp",
  source_identity AS "User",
  on_behalf_of_user_id AS "IDC User ID",
  workgroup AS "Workgroup",
  query AS "SQL Query",
  database AS "Database",
  status AS "Status",
  data_scanned / 1048576.0 AS "Data Scanned (MB)",
  cost AS "Cost (USD)",
  source_ip AS "Source IP",
  day AS "Day"
FROM athena_events.events;
```

---

## Option 4: Grafana Dashboard

**Best for:** Teams already using Grafana for monitoring

- Athena data source plugin available
- Real-time dashboards with alerting (e.g., alert on failed queries)
- Can combine with other AWS metrics

**Cost:** Free (self-hosted) or Grafana Cloud pricing  
**Effort:** Medium

---

## Option 5: Custom Web Application

**Best for:** Organizations needing a fully branded, custom audit portal

- Static HTML/JS page hosted in S3 + CloudFront
- Backend: API Gateway + Lambda calling Athena
- Full control over UI, authentication, and access control

**Cost:** Minimal (S3 + API Gateway + Lambda)  
**Effort:** High

---

## Recommendation

| Audience | Recommended Option |
|---|---|
| Data engineers / analysts | Option 1 (Jupyter Notebook) |
| Business data custodians (small team) | Option 2 (QuickSight) |
| Self-service SQL users | Option 3 (Athena Views) |
| Ops teams with existing Grafana | Option 4 (Grafana) |
| Large organization, compliance portal | Option 5 (Custom Web App) |

Start with **Option 1** (included, free) for immediate use. Graduate to **Option 2** (QuickSight) when the audience grows beyond technical users.
