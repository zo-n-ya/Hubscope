# Hubscope — Batch Logistics Analysis System

## Overview

Hubscope is a logistics analytics system designed for batch-level analysis across multiple warehouses.

It replaces manual spreadsheet workflows by automating data aggregation, grouping, and reporting processes.

---

## Core Modules

### Tool A — Region Summary

Analyzes batch data by region and generates warehouse distribution summaries.

**Input**

* Region identifier
* Batch reference

**Output**

* Warehouse counts
* Percentage breakdown
* Excel-ready numeric blocks

---

### Tool B — Aggregation

Performs multi-batch analysis and generates structured warehouse insights.

**Input**

* Multiple batch references

**Output**

* Warehouse-level detail counts
* Main warehouse totals
* Grouped summaries (main + sub-level)
* Excel export (single sheet)

---

## Key Features

* Batch-based data analysis
* Multi-warehouse aggregation
* Route-based warehouse resolution
* Parallel data fetching
* Built-in caching for performance
* Excel export for reporting

---

## System Design

* Backend: Flask
* Data Processing: Pandas
* Concurrency: ThreadPoolExecutor
* Integration: External APIs
* Output: HTML + Excel

---

## How It Works

1. User inputs batch reference(s)
2. System retrieves data via API
3. Data is processed and deduplicated
4. Warehouse mapping logic is applied
5. Results are aggregated and displayed
6. Optional Excel export is generated

---

## Project Structure

```
hubscope/
├── app.py
└── templates/
    └── index.html
```

---

## Notes

* This is a public-safe version of an internal tool
* API endpoints and credentials are not included
* Warehouse names and routing logic are simplified

---
---
