# 🔐 BEACON SSO Administration Guide

This document outlines how to configure Single Sign-On (SSO) providers and Role-Based Access Control (RBAC) for the Morrow Edge | BEACON ecosystem.

## 1. Provider Configuration
You can enable external identity providers from the **CoolAdmin** dashboard. 

### Supported Providers:
- **Microsoft Entra ID (Azure AD)**
- **Google Workspace**
- **Clever** (For K-12 Districts)
- **On-Premise ActiveDirectory**

### Required Fields:
- **Client ID**: The unique identifier for the registered BEACON app in the external tenant.
- **Client Secret**: The secure string used to authenticate the backend requests.
- **Tenant ID / Metadata URL**: Required for Microsoft and AD integrations to discover the correct endpoints.

*Note: The user-facing SSO Splash screen will NOT be displayed unless at least one provider is checked as "Enabled".*

## 2. RBAC & Native Roles
BEACON supports a strict access matrix to separate concerns between clients, sales, and administration.

### Native Bypasses
To ensure that lockouts do not occur during active deployments, the following roles can authenticate without an SSO handoff:
- `admin`: Full system control and destructive capabilities.
- `sales`: Can access the dashboard, view metrics, and access pitch materials but cannot alter endpoints or drop databases.

### Role Matrix
When adding a user in the RBAC configuration, you grant them access to specific realms:
- **Admin**: Full Access to `/cooladmin` and `/admin`.
- **Editor**: Limited access to `/admin` to modify slides and deploy emergency flares.
- **Viewer**: Read-only access to `/admin` metrics.