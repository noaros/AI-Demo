# Passwords and Multi-Factor Authentication (IT-POL-002)

## Password rules
Contoso passwords must be at least 14 characters long. There is no forced expiry: you only have to change your password if it may have been exposed. You cannot reuse any of your last 10 passwords.

## Multi-factor authentication
MFA is required for every Contoso account. The only approved method is the Microsoft Authenticator app with number matching. SMS and voice calls are not allowed as MFA methods. Hardware FIDO2 keys are available for staff who cannot use a phone; request one in the IT portal.

## Self-service password reset
Reset a forgotten password at `passwordreset.contoso.com`. You need to have registered your Authenticator app first. After 5 failed sign-in attempts your account locks for 15 minutes, then unlocks automatically.

## When to call the Service Desk
Call the Service Desk if your account stays locked after 15 minutes, if you have a new phone and lost access to Authenticator, or if you think someone else knows your password. For a suspected compromise, change your password immediately and report it to security (see IT-POL-004).
