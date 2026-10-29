import * as AuthSession from 'expo-auth-session';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { AzureProfile } from '@/connection/types';
import { Button } from '@/ui/Button';
import { Text } from '@/ui/Text';

export function TransientSignInButton({ profile, onToken }: { profile: AzureProfile; onToken(token: string): void }) {
  const { t } = useTranslation();
  const discovery = AuthSession.useAutoDiscovery(profile.issuer);
  const redirectUri = AuthSession.makeRedirectUri({ scheme: 'nodo', path: 'azure-sync' });
  const [request, response, promptAsync] = AuthSession.useAuthRequest(
    {
      clientId: profile.clientId,
      redirectUri,
      responseType: AuthSession.ResponseType.Code,
      scopes: [profile.apiScope, 'openid', 'profile'],
      usePKCE: true,
    },
    discovery,
  );
  const [error, setError] = useState(false);

  useEffect(() => {
    if (response?.type !== 'success' || !response.params.code || !request?.codeVerifier || !discovery) return;
    void AuthSession.exchangeCodeAsync(
      {
        clientId: profile.clientId,
        code: response.params.code,
        redirectUri,
        extraParams: { code_verifier: request.codeVerifier },
      },
      discovery,
    ).then(
      (token) => {
        setError(false);
        onToken(token.accessToken);
      },
      () => setError(true),
    );
  }, [discovery, onToken, profile.clientId, redirectUri, request, response]);

  const signIn = useCallback(() => {
    setError(false);
    void promptAsync().catch(() => setError(true));
  }, [promptAsync]);

  return (
    <>
      <Button
        variant="secondary"
        label={t('settings.sync.signIn')}
        disabled={!request || !discovery}
        onPress={signIn}
      />
      {error ? <Text tone="danger">{t('settings.sync.signInError')}</Text> : null}
    </>
  );
}
