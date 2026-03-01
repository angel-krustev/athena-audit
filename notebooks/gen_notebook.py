#!/usr/bin/env python3
"""Generate audit_dashboard.ipynb with guaranteed valid JSON."""
import json
import os

nb = {
    "nbformat": 4,
    "nbformat_minor": 4,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.12.0"},
    },
    "cells": [],
}


def md(source_str):
    nb["cells"].append({"cell_type": "markdown", "metadata": {}, "source": source_str})


def code(source_str):
    nb["cells"].append(
        {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": source_str}
    )


# ── Cell 1: Title ──────────────────────────────────────────────
md(
    "# Athena Audit Dashboard\n"
    "\n"
    "Interactive notebook for querying Athena audit data (IDC workgroups).\n"
    "\n"
    "1. Run **Cell 2** (authenticate) and **Cell 3** (connect)\n"
    "2. Edit the **Filters** in Cell 4, then run the action cells below\n"
    "\n"
    "Requirements: `pip install pyathena pandas plotly cihi_auth`"
)

# ── Cell 2: IDC Authentication ─────────────────────────────────
code(
    "# === Step 1: IDC Authentication ===\n"
    "from cihi_auth.jupyter_helper import authenticate, get_session\n"
    "\n"
    "authenticate()\n"
    "session = get_session(profile='default')\n"
    "print('Authenticated via TIP')"
)

# ── Cell 3: Connect to Athena ──────────────────────────────────
code(
    "# === Step 2: Connect to Athena ===\n"
    "import warnings\n"
    "warnings.filterwarnings('ignore')\n"
    "\n"
    "import pandas as pd\n"
    "import plotly.express as px\n"
    "from pyathena import connect\n"
    "from IPython.display import display\n"
    "\n"
    "REGION = 'us-east-1'\n"
    "DATABASE = 'athena_events'\n"
    "TABLE = 'events'\n"
    "WORKGROUP = 'idc-wg'\n"
    "\n"
    "# Athena pricing - adjust if your pricing differs\n"
    "COST_PER_TB = 5.00  # USD per TB scanned (change to match your rate)\n"
    "\n"
    "creds = session.get_credentials().get_frozen_credentials()\n"
    "conn = connect(\n"
    "    region_name=REGION,\n"
    "    work_group=WORKGROUP,\n"
    "    schema_name=DATABASE,\n"
    "    aws_access_key_id=creds.access_key,\n"
    "    aws_secret_access_key=creds.secret_key,\n"
    "    aws_session_token=creds.token,\n"
    ")\n"
    "print('Connected to Athena (' + WORKGROUP + ')')"
)

# ── Cell 4: Filters ────────────────────────────────────────────
md(
    "---\n"
    "## Filters\n"
    "Edit the values below and run the cell. Then run any action cell underneath."
)

code(
    "# === EDIT YOUR FILTERS HERE, then run this cell ===\n"
    "\n"
    "FROM_DATE = '2026-02-22'   # start date (YYYY-MM-DD)\n"
    "TO_DATE   = '2026-03-01'   # end date (YYYY-MM-DD)\n"
    "USER      = ''             # source_identity filter (leave empty for all)\n"
    "WORKGROUP_FILTER = ''      # workgroup filter (leave empty for all)\n"
    "STATUS    = ''             # SUCCEEDED, FAILED, CANCELLED (leave empty for all)\n"
    "QUERY_CONTAINS = ''        # text search in query (leave empty for all)\n"
    "MAX_ROWS  = 200            # max rows to return\n"
    "\n"
    "# --- Build WHERE clause (do not edit below) ---\n"
    "def _where():\n"
    "    c = [\"day BETWEEN '\" + FROM_DATE + \"' AND '\" + TO_DATE + \"'\"]\n"
    "    if USER:\n"
    "        c.append(\"source_identity = '\" + USER + \"'\")\n"
    "    if WORKGROUP_FILTER:\n"
    "        c.append(\"workgroup = '\" + WORKGROUP_FILTER + \"'\")\n"
    "    if STATUS:\n"
    "        c.append(\"status = '\" + STATUS + \"'\")\n"
    "    if QUERY_CONTAINS:\n"
    "        c.append(\"LOWER(query) LIKE '%\" + QUERY_CONTAINS.lower() + \"%'\")\n"
    "    return ' AND '.join(c)\n"
    "\n"
    "print('Filters set:')\n"
    "print('  Date range: ' + FROM_DATE + ' to ' + TO_DATE)\n"
    "print('  User:       ' + (USER or '(all)'))\n"
    "print('  Workgroup:  ' + (WORKGROUP_FILTER or '(all)'))\n"
    "print('  Status:     ' + (STATUS or '(all)'))\n"
    "print('  Query text: ' + (QUERY_CONTAINS or '(any)'))\n"
    "print('  Max rows:   ' + str(MAX_ROWS))"
)

# ── Cell 5: List available users ────────────────────────────────
md("---\n## Available Users\nRun this cell to see what users/workgroups exist in the data.")

code(
    "# === List available users and workgroups ===\n"
    "users = pd.read_sql(\n"
    "    \"SELECT source_identity, COUNT(*) AS queries \"\n"
    "    \"FROM \" + TABLE + \" \"\n"
    "    \"WHERE source_identity IS NOT NULL \"\n"
    "    \"AND day BETWEEN '\" + FROM_DATE + \"' AND '\" + TO_DATE + \"' \"\n"
    "    \"GROUP BY 1 ORDER BY 2 DESC\", conn\n"
    ")\n"
    "wgs = pd.read_sql(\n"
    "    \"SELECT workgroup, COUNT(*) AS queries \"\n"
    "    \"FROM \" + TABLE + \" \"\n"
    "    \"WHERE workgroup IS NOT NULL \"\n"
    "    \"AND day BETWEEN '\" + FROM_DATE + \"' AND '\" + TO_DATE + \"' \"\n"
    "    \"GROUP BY 1 ORDER BY 2 DESC\", conn\n"
    ")\n"
    "print('=== USERS ===')\n"
    "display(users)\n"
    "print()\n"
    "print('=== WORKGROUPS ===')\n"
    "display(wgs)"
)

# ── Cell 6: User Queries (PRIMARY) ──────────────────────────────
md(
    "---\n"
    "## User Queries (Primary View)\n"
    "Shows queries run by the selected user with status and query text.\n"
    "Set `USER` in the Filters cell above, then run this cell."
)

code(
    "# === USER QUERIES ===\n"
    "sql = (\n"
    "    'SELECT event_time, source_identity, workgroup, status, '\n"
    "    'SUBSTR(query, 1, 300) AS query_text, '\n"
    "    '\"database\", ROUND(data_scanned / 1048576.0, 2) AS data_mb, '\n"
    "    'ROUND(cost, 6) AS cost_usd '\n"
    "    'FROM ' + TABLE + ' '\n"
    "    'WHERE ' + _where() + ' '\n"
    "    'ORDER BY event_time DESC '\n"
    "    'LIMIT ' + str(MAX_ROWS)\n"
    ")\n"
    "\n"
    "df = pd.read_sql(sql, conn)\n"
    "\n"
    "if df.empty:\n"
    "    print('No results. Try adjusting filters.')\n"
    "else:\n"
    "    print(str(len(df)) + ' queries found')\n"
    "    print()\n"
    "    # Status summary\n"
    "    summary = df.groupby('status').agg(\n"
    "        count=('status', 'size'),\n"
    "        total_mb=('data_mb', 'sum'),\n"
    "        total_cost=('cost_usd', 'sum')\n"
    "    ).reset_index()\n"
    "    print('--- Status Summary ---')\n"
    "    for _, r in summary.iterrows():\n"
    "        print('  ' + str(r['status']) + ': ' + str(int(r['count'])) + ' queries, ' + str(round(r['total_mb'], 2)) + ' MB, $' + str(round(r['total_cost'], 4)))\n"
    "    total_cost = df['cost_usd'].sum()\n"
    "    print()\n"
    "    print('  TOTAL COST: $' + str(round(total_cost, 4)))\n"
    "    print()\n"
    "    display(df)"
)

# ── Cell 7: Export ──────────────────────────────────────────────
code(
    "# === EXPORT last query results to CSV ===\n"
    "if 'df' in dir() and df is not None and not df.empty:\n"
    "    df.to_csv('audit_export.csv', index=False)\n"
    "    print('Exported ' + str(len(df)) + ' rows to audit_export.csv')\n"
    "else:\n"
    "    print('No data to export. Run User Queries first.')"
)

# ── Cell 8: Dashboard ──────────────────────────────────────────
md(
    "---\n"
    "## Dashboard\n"
    "Summary statistics and charts for the selected filters."
)

code(
    "# === SUMMARY STATS ===\n"
    "w = _where()\n"
    "s = pd.read_sql(\n"
    "    'SELECT COUNT(*) AS total, '\n"
    "    'COUNT(DISTINCT source_identity) AS users, '\n"
    "    'COUNT(DISTINCT workgroup) AS workgroups, '\n"
    "    \"SUM(CASE WHEN status='SUCCEEDED' THEN 1 ELSE 0 END) AS ok, \"\n"
    "    \"SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) AS fail, \"\n"
    "    \"SUM(CASE WHEN status='CANCELLED' THEN 1 ELSE 0 END) AS cancel, \"\n"
    "    'ROUND(SUM(data_scanned)/1073741824.0,3) AS gb, '\n"
    "    'ROUND(SUM(cost), 4) AS total_cost '\n"
    "    'FROM ' + TABLE + ' WHERE ' + w, conn\n"
    ").iloc[0]\n"
    "\n"
    "print('=' * 55)\n"
    "print('  AUDIT SUMMARY: ' + FROM_DATE + ' to ' + TO_DATE)\n"
    "print('=' * 55)\n"
    "print('  Total Queries:   ' + str(int(s['total'])))\n"
    "print('  Unique Users:    ' + str(int(s['users'])))\n"
    "print('  Workgroups:      ' + str(int(s['workgroups'])))\n"
    "print('  Succeeded:       ' + str(int(s['ok'])))\n"
    "print('  Failed:          ' + str(int(s['fail'])))\n"
    "print('  Cancelled:       ' + str(int(s['cancel'])))\n"
    "print('  Data Scanned:    ' + str(s['gb']) + ' GB')\n"
    "print('  Total Cost:      $' + str(s['total_cost']))\n"
    "print('=' * 55)"
)

# ── Cell 9: Queries per user chart ──────────────────────────────
code(
    "# === QUERIES PER USER by Status ===\n"
    "udf = pd.read_sql(\n"
    "    'SELECT COALESCE(source_identity, user_identity_type) AS user_name, '\n"
    "    'status, COUNT(*) AS cnt '\n"
    "    'FROM ' + TABLE + ' WHERE ' + _where() + ' '\n"
    "    'GROUP BY 1, 2 ORDER BY cnt DESC LIMIT 50', conn\n"
    ")\n"
    "if not udf.empty:\n"
    "    fig = px.bar(udf, x='user_name', y='cnt', color='status',\n"
    "                 title='Queries Per User by Status',\n"
    "                 labels={'user_name': 'User', 'cnt': 'Count', 'status': 'Status'},\n"
    "                 color_discrete_map={'SUCCEEDED': '#2ecc71', 'FAILED': '#e74c3c', 'CANCELLED': '#f39c12'},\n"
    "                 barmode='stack')\n"
    "    fig.update_layout(xaxis_tickangle=-45, height=400)\n"
    "    fig.show()\n"
    "else:\n"
    "    print('No data for chart')"
)

# ── Cell 10: Status pie ─────────────────────────────────────────
code(
    "# === STATUS BREAKDOWN ===\n"
    "sdf = pd.read_sql(\n"
    "    \"SELECT COALESCE(status, 'UNKNOWN') AS status, COUNT(*) AS cnt \"\n"
    "    'FROM ' + TABLE + ' WHERE ' + _where() + ' '\n"
    "    'GROUP BY 1', conn\n"
    ")\n"
    "if not sdf.empty:\n"
    "    fig = px.pie(sdf, names='status', values='cnt', title='Status Breakdown',\n"
    "                 color='status',\n"
    "                 color_discrete_map={'SUCCEEDED': '#2ecc71', 'FAILED': '#e74c3c', 'CANCELLED': '#f39c12', 'UNKNOWN': '#95a5a6'})\n"
    "    fig.update_traces(textinfo='label+percent+value')\n"
    "    fig.update_layout(height=400)\n"
    "    fig.show()\n"
    "else:\n"
    "    print('No data for chart')"
)

# ── Cell 11: Timeline ───────────────────────────────────────────
code(
    "# === QUERIES PER DAY ===\n"
    "tdf = pd.read_sql(\n"
    "    'SELECT day, workgroup, COUNT(*) AS cnt '\n"
    "    'FROM ' + TABLE + ' WHERE ' + _where() + ' '\n"
    "    'GROUP BY 1, 2 ORDER BY 1', conn\n"
    ")\n"
    "if not tdf.empty:\n"
    "    fig = px.bar(tdf, x='day', y='cnt', color='workgroup',\n"
    "                 title='Queries Per Day by Workgroup', barmode='stack',\n"
    "                 labels={'day': 'Day', 'cnt': 'Count', 'workgroup': 'Workgroup'})\n"
    "    fig.update_layout(height=400)\n"
    "    fig.show()\n"
    "else:\n"
    "    print('No data for chart')"
)

# ── Cell 12: Data scanned ──────────────────────────────────────
code(
    "# === DATA SCANNED & COST PER WORKGROUP ===\n"
    "ddf = pd.read_sql(\n"
    "    'SELECT workgroup, COUNT(*) AS cnt, '\n"
    "    'ROUND(SUM(data_scanned)/1073741824.0, 3) AS gb, '\n"
    "    'ROUND(SUM(cost), 4) AS cost_usd '\n"
    "    'FROM ' + TABLE + ' WHERE ' + _where() + ' AND workgroup IS NOT NULL '\n"
    "    'GROUP BY 1 ORDER BY cost_usd DESC', conn\n"
    ")\n"
    "if not ddf.empty:\n"
    "    print('Cost per workgroup:')\n"
    "    for _, r in ddf.iterrows():\n"
    "        print('  ' + str(r['workgroup']) + ': ' + str(int(r['cnt'])) + ' queries, ' + str(r['gb']) + ' GB, $' + str(r['cost_usd']))\n"
    "    print()\n"
    "    fig = px.bar(ddf, x='workgroup', y='cost_usd', text='cnt',\n"
    "                 title='Cost Per Workgroup (USD)',\n"
    "                 labels={'workgroup': 'Workgroup', 'cost_usd': 'Cost ($)', 'cnt': 'Queries'},\n"
    "                 color='cost_usd', color_continuous_scale='Reds')\n"
    "    fig.update_traces(texttemplate='%{text} queries', textposition='outside')\n"
    "    fig.update_layout(height=400)\n"
    "    fig.show()\n"
    "else:\n"
    "    print('No data for chart')"
)

# ── Cell 13: Cost per user ──────────────────────────────────────
code(
    "# === COST PER USER ===\n"
    "cdf = pd.read_sql(\n"
    "    'SELECT COALESCE(source_identity, user_identity_type) AS user_name, '\n"
    "    'COUNT(*) AS queries, '\n"
    "    'ROUND(SUM(data_scanned)/1073741824.0, 3) AS gb, '\n"
    "    'ROUND(SUM(cost), 4) AS cost_usd '\n"
    "    'FROM ' + TABLE + ' WHERE ' + _where() + ' '\n"
    "    'GROUP BY 1 ORDER BY cost_usd DESC LIMIT 30', conn\n"
    ")\n"
    "if not cdf.empty:\n"
    "    print('Top users by cost:')\n"
    "    for _, r in cdf.iterrows():\n"
    "        print('  ' + str(r['user_name']) + ': ' + str(int(r['queries'])) + ' queries, ' + str(r['gb']) + ' GB, $' + str(r['cost_usd']))\n"
    "    print()\n"
    "    fig = px.bar(cdf, x='user_name', y='cost_usd', text='queries',\n"
    "                 title='Cost Per User (USD)',\n"
    "                 labels={'user_name': 'User', 'cost_usd': 'Cost ($)', 'queries': 'Queries'},\n"
    "                 color='cost_usd', color_continuous_scale='Blues')\n"
    "    fig.update_traces(texttemplate='%{text} queries', textposition='outside')\n"
    "    fig.update_layout(xaxis_tickangle=-45, height=400)\n"
    "    fig.show()\n"
    "else:\n"
    "    print('No data for chart')"
)

# ── Cell 14: Ad-hoc ─────────────────────────────────────────────
md("---\n## Ad-Hoc SQL\nModify the SQL below and run the cell.")

code(
    "# === Custom SQL ===\n"
    "custom_sql = \"\"\"\n"
    "SELECT source_identity, workgroup, status, COUNT(*) AS cnt,\n"
    "       ROUND(SUM(data_scanned) / 1048576.0, 2) AS total_mb,\n"
    "       ROUND(SUM(cost), 4) AS total_cost_usd\n"
    "FROM events\n"
    "WHERE day >= '2026-02-28'\n"
    "  AND source_identity IS NOT NULL\n"
    "GROUP BY source_identity, workgroup, status\n"
    "ORDER BY cnt DESC\n"
    "LIMIT 50\n"
    "\"\"\"\n"
    "\n"
    "result = pd.read_sql(custom_sql, conn)\n"
    "display(result)"
)

# ── Write ───────────────────────────────────────────────────────
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit_dashboard.ipynb")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(nb, f, indent=1, ensure_ascii=False)

print("Written " + str(len(nb["cells"])) + " cells to " + out_path)
print("File size: " + str(os.path.getsize(out_path)) + " bytes")
