# NODO mobile

The Expo app defaults to local sample data. All public runtime values are read from Expo environment variables.

## Configuration

- `EXPO_PUBLIC_DATA_SOURCE` - `mock` by default, or `cloud`
- `EXPO_PUBLIC_API_BASE_URL` - API base URL, required for cloud mode
- `EXPO_PUBLIC_OIDC_ISSUER` - Entra External ID issuer URL
- `EXPO_PUBLIC_OIDC_CLIENT_ID` - public client application ID
- `EXPO_PUBLIC_OIDC_API_SCOPE` - delegated API scope, for example `api://<api-client-id>/access_as_user`
- `EXPO_PUBLIC_DEV_TOKEN` - development-only static bearer token for a local API

Cloud URLs must use HTTPS. HTTP is accepted only for `localhost`, `127.0.0.1`, and the Android emulator host alias `10.0.2.2`.

## Run with sample data

```powershell
$env:EXPO_PUBLIC_DATA_SOURCE='mock'
npx expo start
```

## Run against a local API

Start the API in its development authentication mode with matching tokens:

```powershell
$env:NODO_ENVIRONMENT='dev'
$env:NODO_AUTH_MODE='dev'
$env:NODO_DEV_TOKEN='<local-development-token>'
```

Then start the app. Use `10.0.2.2` instead of `localhost` from an Android emulator.

```powershell
$env:EXPO_PUBLIC_DATA_SOURCE='cloud'
$env:EXPO_PUBLIC_API_BASE_URL='http://10.0.2.2:8000'
$env:EXPO_PUBLIC_DEV_TOKEN='<local-development-token>'
npx expo start
```

`EXPO_PUBLIC_DEV_TOKEN` is ignored in production builds. Never use a production credential in this variable.

## Entra External ID

Create a public client app registration for the mobile app and configure authorization code flow with PKCE. Register `nodo://auth` as the mobile redirect URI. Grant the app the delegated API scope exposed by the NODO API, then set that full scope value in `EXPO_PUBLIC_OIDC_API_SCOPE`. The app also requests `openid`, `profile`, and `offline_access` so it can identify the session and refresh access tokens.

Set the issuer, client ID, API scope, HTTPS API URL, and `EXPO_PUBLIC_DATA_SOURCE=cloud` before creating a development or release build. No client secret belongs in the app.
