# 🏫 Setting Up SSO for BEACON (Client Guide)

Welcome to Morrow Edge | BEACON! To allow your staff to securely manage your municipality or school district's slides and emergency alerts, we need to connect BEACON to your existing Identity Provider (SSO).

It is designed to be straightforward for your IT department to implement.

## Step 1: Register the Application
Log in to your Identity Provider (e.g., Microsoft Entra Admin Center, Google Admin Console, or Clever Dashboard).

1. Navigate to **App Registrations** or **API Controls**.
2. Create a New Application/Registration.
3. Name it: `BEACON Dashboard`.
4. Set the **Redirect URI (Callback URL)** to the URL provided by your Morrow Edge sales representative (e.g., `https://beacon.yourdomain.org/auth/callback`).

## Step 2: Gather Your Credentials
Once the app is registered, you need to securely provide the following three pieces of information to your BEACON Administrator:

1. **Client ID** (or Application ID)
2. **Client Secret** (Generate a new secret and copy the value immediately—it will be hidden later!)
3. **Tenant ID** (Required for Microsoft) or **Metadata URL** (Required for On-Premise AD/SAML).

## Step 3: Role Assignment
Once connected, users who navigate to your BEACON dashboard will be prompted to "Sign In". Their access level is managed centrally by BEACON. Please provide a list of Principal Names/Emails to your Morrow Edge representative so they can be assigned the appropriate roles within the matrix.