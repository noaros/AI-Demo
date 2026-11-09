# VPN and Remote Access (IT-POL-001)

## Who needs the VPN
Contoso employees must connect to the VPN before using internal systems from outside a Contoso office. This includes the HR portal, the finance system and internal file shares. Microsoft 365 apps (Outlook, Teams, OneDrive) work without the VPN.

## Client and portal
Contoso uses GlobalProtect. It is preinstalled on every Contoso-managed laptop. Employees connect to the portal `vpn.contoso.com` and sign in with their Contoso account and MFA.

## Contractors
Contractors may use the VPN only through the separate portal `partners-vpn.contoso.com`. A Contoso manager must sponsor the request in the IT portal. Contractor VPN access expires after 90 days and must be renewed by the sponsor. Contractors may not connect from personal devices; they must use a Contoso-issued laptop or a device approved under IT-POL-006.

## Restrictions
Split tunnelling is disabled: all traffic goes through Contoso while the VPN is connected. The VPN disconnects after 12 hours and you must sign in again. Connecting from countries on the export-restricted list is blocked; contact the Service Desk before travelling.
