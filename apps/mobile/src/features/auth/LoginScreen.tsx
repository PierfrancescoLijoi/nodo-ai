import { SignIn } from '@/ui/icons';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useAuth } from '@/auth/AuthProvider';
import { Button } from '@/ui/Button';
import { Screen } from '@/ui/Screen';
import { Text } from '@/ui/Text';

export default function LoginScreen() {
  const { t } = useTranslation();
  const { signIn, error } = useAuth();
  const [signingIn, setSigningIn] = useState(false);
  const start = async () => {
    setSigningIn(true);
    try {
      await signIn();
    } finally {
      setSigningIn(false);
    }
  };
  return (
    <Screen wordmark title={t('login.title')} intro={t('login.body')} bottomInset>
      {error ? <Text tone="danger">{t('login.error')}</Text> : null}
      <Button
        fullWidth
        icon={SignIn}
        disabled={signingIn}
        label={t(signingIn ? 'login.signingIn' : 'login.signIn')}
        onPress={() => void start()}
      />
    </Screen>
  );
}
