# Hubscope

Internal ops tool for batch analysis

----------------------------------------

ACCESS

http://104.248.10.126:5050/

----------------------------------------

FEATURES

Tool A — Region Summary

Input:
REGION BATCH


Output:
- Subwarehouse count (ATL / BHM / BFM / ...)
- Percentage
- Numbers-only block for Excel

----------------------------------------


Tool B — Aggregation

Input:
Multiple Batch

Output:
- Warehouse detail counts
- Main warehouse totals


Download:
- Excel
- Single sheet

----------------------------------------

SECURITY

- Browser “remember” stores plaintext locally

----------------------------------------

STRUCTURE

hubscope/
├── app.py
└── templates/
    └── index.html
