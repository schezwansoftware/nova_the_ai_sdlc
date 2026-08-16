"""Shared Atlassian (Jira + Confluence) site config and HTTP scaffolding.

Jira and Confluence ship as two separate standalone MCP server processes/
entry points (`ai-sdlc-mcp-jira`, `ai-sdlc-mcp-confluence`, per the
approved design), but they're the same product family with the same
site-URL/auth shape underneath -- see `auth.py`'s module docstring for
the full account of what's shared here and why.
"""
