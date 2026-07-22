import { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, Text, TextInput, View } from 'react-native';

import { BrandMark } from '@/components/brand-mark';
import { ScreenScroll } from '@/components/screen-scroll';
import { SectionHeading } from '@/components/section-heading';
import { StatusPill } from '@/components/status-pill';
import { SurfaceCard } from '@/components/surface-card';
import { radii } from '@/constants/theme';
import { useAppTheme } from '@/hooks/use-app-theme';
import { useMongars } from '@/providers/mongars-provider';

type ConnectionState = 'idle' | 'testing' | 'ready' | 'error';

export default function SettingsScreen() {
  const theme = useAppTheme();
  const {
    baseUrl,
    baseUrlStatus,
    baseUrlStorageError,
    client,
    clearToken,
    configurationError,
    hasToken,
    saveBaseUrl,
    saveToken,
    tokenStatus,
    tokenStorageError,
    transportSecurity,
  } = useMongars();
  const [serverUrl, setServerUrl] = useState(baseUrl ?? '');
  const [serverUrlSaving, setServerUrlSaving] = useState(false);
  const [serverUrlMessage, setServerUrlMessage] = useState<string | null>(null);
  const [token, setToken] = useState('');
  const [connectionState, setConnectionState] = useState<ConnectionState>('idle');
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const credentialTransportAllowed = transportSecurity?.canSendCredentials === true;
  const canTest =
    Boolean(client) && credentialTransportAllowed && (Boolean(token.trim()) || hasToken);

  useEffect(() => {
    if (baseUrl && !serverUrl) {
      setServerUrl(baseUrl);
    }
  }, [baseUrl, serverUrl]);

  async function saveServerUrl() {
    if (!serverUrl.trim() || serverUrlSaving) return;
    setServerUrlSaving(true);
    setServerUrlMessage(null);
    setConnectionError(null);
    try {
      const saved = await saveBaseUrl(serverUrl);
      setServerUrl(saved);
      setToken('');
      setConnectionState('idle');
      setServerUrlMessage(
        'Server saved on this device. Enter its API token to verify the connection.',
      );
    } catch (error) {
      setConnectionState('error');
      setConnectionError(error instanceof Error ? error.message : 'Unable to save server URL.');
    } finally {
      setServerUrlSaving(false);
    }
  }

  async function saveAndTestConnection() {
    if (!client || !credentialTransportAllowed || (!token.trim() && !hasToken)) return;
    setConnectionState('testing');
    setConnectionError(null);
    try {
      if (token.trim()) {
        await saveToken(token);
      }
      const readiness = await client.readiness();
      if (readiness.status !== 'ready') {
        throw new Error('The control plane is reachable, but one or more dependencies are not ready.');
      }
      await client.listTasks(1);
      setToken('');
      setConnectionState('ready');
    } catch (error) {
      setConnectionState('error');
      setConnectionError(error instanceof Error ? error.message : 'Connection test failed.');
    }
  }

  async function forgetToken() {
    try {
      await clearToken();
      setToken('');
      setConnectionState('idle');
      setConnectionError(null);
    } catch (error) {
      setConnectionState('error');
      setConnectionError(error instanceof Error ? error.message : 'Unable to clear the token.');
    }
  }

  return (
    <ScreenScroll>
      <SurfaceCard tone="primary">
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 14 }}>
          <BrandMark />
          <View style={{ flex: 1, alignItems: 'flex-end', gap: 5 }}>
            <StatusPill
              label={
                connectionState === 'ready'
                  ? 'Connected'
                  : hasToken
                    ? 'Token saved'
                    : 'Setup'
              }
              tone={connectionState === 'ready' ? 'positive' : hasToken ? 'primary' : 'warning'}
            />
            <Text selectable style={{ color: theme.textSecondary, fontSize: 11 }}>
              Expo SDK 54 · iOS
            </Text>
          </View>
        </View>
      </SurfaceCard>

      <SectionHeading
        detail="Choose your HTTPS control-plane address. The server and token stay in the device Keychain."
        title="Connection"
      />

      <SurfaceCard>
        <View style={{ gap: 7 }}>
          <Text selectable style={{ color: theme.textSecondary, fontSize: 12, fontWeight: '600' }}>
            SERVER URL
          </Text>
          <TextInput
            accessibilityLabel="monGARS server URL"
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="url"
            onChangeText={(value) => {
              setServerUrl(value);
              setServerUrlMessage(null);
              setConnectionError(null);
            }}
            placeholder="https://mongars.example.com"
            placeholderTextColor={theme.textTertiary}
            selectionColor={theme.primary}
            style={{
              backgroundColor: theme.input,
              borderCurve: 'continuous',
              borderRadius: radii.medium,
              color: theme.text,
              fontSize: 14,
              paddingHorizontal: 13,
              paddingVertical: 12,
            }}
            value={serverUrl}
          />
          <Pressable
            accessibilityRole="button"
            disabled={!serverUrl.trim() || serverUrlSaving}
            onPress={() => void saveServerUrl()}
            style={({ pressed }) => ({
              alignItems: 'center',
              backgroundColor: serverUrl.trim() ? theme.primarySoft : theme.surfaceMuted,
              borderRadius: 14,
              opacity: pressed || serverUrlSaving ? 0.72 : 1,
              padding: 12,
            })}
          >
            {serverUrlSaving ? (
              <ActivityIndicator color={theme.primary} />
            ) : (
              <Text
                style={{
                  color: serverUrl.trim() ? theme.primary : theme.textTertiary,
                  fontSize: 14,
                  fontWeight: '700',
                }}
              >
                Save server URL
              </Text>
            )}
          </Pressable>
          <Text selectable style={{ color: theme.textTertiary, fontSize: 11 }}>
            {baseUrlStatus === 'loading'
              ? 'Loading saved server…'
              : baseUrl
                ? `Active: ${baseUrl}`
                : 'No server is configured.'}
          </Text>
        </View>

        <View style={{ gap: 7 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center' }}>
            <Text
              selectable
              style={{ color: theme.textSecondary, flex: 1, fontSize: 12, fontWeight: '600' }}
            >
              API TOKEN
            </Text>
            <Text selectable style={{ color: theme.textTertiary, fontSize: 11 }}>
              {tokenStatus === 'loading'
                ? 'Checking Keychain…'
                : hasToken
                  ? 'Saved in Keychain'
                  : 'Not saved'}
            </Text>
          </View>
          <TextInput
            accessibilityLabel="monGARS API token"
            autoCapitalize="none"
            autoCorrect={false}
            editable={credentialTransportAllowed}
            onChangeText={setToken}
            placeholder={hasToken ? 'Enter a replacement token' : 'Paste bearer token'}
            placeholderTextColor={theme.textTertiary}
            secureTextEntry
            selectionColor={theme.primary}
            style={{
              backgroundColor: theme.input,
              borderCurve: 'continuous',
              borderRadius: radii.medium,
              color: theme.text,
              fontSize: 14,
              opacity: credentialTransportAllowed ? 1 : 0.55,
              paddingHorizontal: 13,
              paddingVertical: 12,
            }}
            value={token}
          />
        </View>

        <Pressable
          accessibilityRole="button"
          disabled={!canTest || connectionState === 'testing'}
          onPress={() => void saveAndTestConnection()}
          style={({ pressed }) => ({
            alignItems: 'center',
            backgroundColor: canTest ? theme.primary : theme.surfaceMuted,
            borderRadius: 14,
            opacity: pressed || connectionState === 'testing' ? 0.72 : 1,
            padding: 13,
          })}
        >
          {connectionState === 'testing' ? (
            <ActivityIndicator color={theme.primaryContrast} />
          ) : (
            <Text
              style={{
                color: canTest ? theme.primaryContrast : theme.textTertiary,
                fontSize: 14,
                fontWeight: '700',
              }}
            >
              {token.trim() ? 'Save and test connection' : 'Test saved connection'}
            </Text>
          )}
        </Pressable>

        {hasToken ? (
          <Pressable accessibilityRole="button" onPress={() => void forgetToken()}>
            <Text style={{ color: theme.danger, fontSize: 13, fontWeight: '600', textAlign: 'center' }}>
              Forget saved token
            </Text>
          </Pressable>
        ) : null}
      </SurfaceCard>

      {serverUrlMessage ? (
        <SurfaceCard tone="positive" title="Server updated">
          <Text selectable style={{ color: theme.positive, fontSize: 13, lineHeight: 19 }}>
            {serverUrlMessage}
          </Text>
        </SurfaceCard>
      ) : null}

      {configurationError ? (
        <SurfaceCard tone="danger" title="Invalid server URL">
          <Text selectable style={{ color: theme.danger, fontSize: 13, lineHeight: 19 }}>
            {configurationError.message}
          </Text>
        </SurfaceCard>
      ) : !baseUrl && baseUrlStatus !== 'loading' ? (
        <SurfaceCard tone="warning" title="Connect monGARS">
          <Text selectable style={{ color: theme.warning, fontSize: 13, lineHeight: 19 }}>
            Enter the HTTPS address of your monGARS control plane above, then save it on this
            device.
          </Text>
        </SurfaceCard>
      ) : baseUrl && !credentialTransportAllowed ? (
        <SurfaceCard tone="danger" title="Credential transport blocked">
          <Text selectable style={{ color: theme.danger, fontSize: 13, lineHeight: 19 }}>
            {transportSecurity?.message ?? 'Use HTTPS before saving a bearer token.'}
          </Text>
        </SurfaceCard>
      ) : null}

      {connectionError || tokenStorageError || baseUrlStorageError ? (
        <SurfaceCard tone="danger" title="Connection failed">
          <Text selectable style={{ color: theme.danger, fontSize: 13, lineHeight: 19 }}>
            {connectionError ?? tokenStorageError?.message ?? baseUrlStorageError?.message}
          </Text>
        </SurfaceCard>
      ) : null}

      {connectionState === 'ready' ? (
        <SurfaceCard tone="positive" title="Control plane verified">
          <Text selectable style={{ color: theme.positive, fontSize: 13, lineHeight: 19 }}>
            Readiness and authenticated task access both succeeded.
          </Text>
        </SurfaceCard>
      ) : null}

      <SectionHeading detail="Control how this client may use inference." title="Privacy" />
      <SurfaceCard>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 14 }}>
          <View style={{ flex: 1, gap: 4 }}>
            <Text selectable style={{ color: theme.text, fontSize: 16, fontWeight: '600' }}>
              Require local inference
            </Text>
            <Text selectable style={{ color: theme.textSecondary, fontSize: 13, lineHeight: 18 }}>
              Prevent requests from using a remote fallback endpoint.
            </Text>
          </View>
          <StatusPill label="Required" tone="positive" />
        </View>
      </SurfaceCard>
    </ScreenScroll>
  );
}
