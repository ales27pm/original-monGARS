import { useState } from 'react';
import { ActivityIndicator, Pressable, Text, TextInput, View } from 'react-native';

import { BrandMark } from '@/components/brand-mark';
import { ScreenScroll } from '@/components/screen-scroll';
import { SectionHeading } from '@/components/section-heading';
import { StatusPill } from '@/components/status-pill';
import { SurfaceCard } from '@/components/surface-card';
import { radii } from '@/constants/theme';
import { useAppTheme } from '@/hooks/use-app-theme';
import { useReadiness } from '@/hooks/use-mongars-api';
import { isActiveMongarsApiBaseUrlDraft } from '@/lib/api-origin';
import {
  readinessBadge,
  readinessFailureSummary,
  readinessRows,
  type ReadinessRowSummary,
} from '@/lib/readiness-summary';
import { useMongars } from '@/providers/mongars-provider';
import type { ReadinessResponse } from '@/types/mongars-api';

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
  const [serverUrlInput, setServerUrlInput] = useState(baseUrl ?? '');
  const [hasServerUrlEdits, setHasServerUrlEdits] = useState(false);
  const [serverUrlSaving, setServerUrlSaving] = useState(false);
  const [serverUrlMessage, setServerUrlMessage] = useState<string | null>(null);
  const [token, setToken] = useState('');
  const [connectionState, setConnectionState] = useState<ConnectionState>('idle');
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [verifiedReadiness, setVerifiedReadiness] = useState<ReadinessResponse | null>(null);
  const isTestingConnection = connectionState === 'testing';
  const credentialTransportAllowed = transportSecurity?.canSendCredentials === true;
  const serverUrl = hasServerUrlEdits ? serverUrlInput : baseUrl ?? '';
  const draftMatchesActiveBaseUrl = isActiveMongarsApiBaseUrlDraft(serverUrl, baseUrl);
  const canEditToken =
    credentialTransportAllowed && draftMatchesActiveBaseUrl && !isTestingConnection;
  const canTest =
    Boolean(client) &&
    credentialTransportAllowed &&
    draftMatchesActiveBaseUrl &&
    !serverUrlSaving &&
    (Boolean(token.trim()) || hasToken);
  const readiness = useReadiness({
    auto: Boolean(client && hasToken && credentialTransportAllowed && draftMatchesActiveBaseUrl),
  });
  const displayedReadiness =
    draftMatchesActiveBaseUrl && credentialTransportAllowed
      ? readiness.data ?? verifiedReadiness
      : null;
  const displayedReadinessBadge = displayedReadiness
    ? readinessBadge(displayedReadiness)
    : { label: 'Needs attention', tone: 'warning' as const };
  const inferenceHealthy = displayedReadiness?.dependencies.inference.healthy;
  const executorRequiresApproval =
    displayedReadiness?.dependencies.executor_security?.requires_approval;

  async function saveServerUrl() {
    if (!serverUrl.trim() || serverUrlSaving || isTestingConnection) return;
    setServerUrlSaving(true);
    setServerUrlMessage(null);
    setConnectionError(null);
    setVerifiedReadiness(null);
    try {
      const saved = await saveBaseUrl(serverUrl);
      setServerUrlInput(saved);
      setHasServerUrlEdits(true);
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
    if (!draftMatchesActiveBaseUrl) {
      setConnectionState('error');
      setConnectionError('Save this server URL before entering or testing its API token.');
      return;
    }
    if (
      !client ||
      !credentialTransportAllowed ||
      serverUrlSaving ||
      isTestingConnection ||
      (!token.trim() && !hasToken)
    ) {
      return;
    }
    setConnectionState('testing');
    setConnectionError(null);
    try {
      if (token.trim()) {
        await saveToken(token);
      }
      const readiness = await client.readiness();
      setVerifiedReadiness(readiness);
      if (readiness.status !== 'ready') {
        throw new Error(readinessFailureSummary(readiness));
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
    if (isTestingConnection) return;
    try {
      await clearToken();
      setToken('');
      setConnectionState('idle');
      setConnectionError(null);
      setVerifiedReadiness(null);
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
            editable={!isTestingConnection}
            keyboardType="url"
            onChangeText={(value) => {
              setServerUrlInput(value);
              setHasServerUrlEdits(true);
              setServerUrlMessage(null);
              setConnectionError(null);
              setConnectionState('idle');
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
            disabled={!serverUrl.trim() || serverUrlSaving || isTestingConnection}
            onPress={() => void saveServerUrl()}
            style={({ pressed }) => ({
              alignItems: 'center',
              backgroundColor: serverUrl.trim() ? theme.primarySoft : theme.surfaceMuted,
              borderRadius: 14,
              opacity: pressed || serverUrlSaving || isTestingConnection ? 0.72 : 1,
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
            editable={canEditToken}
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
              opacity: canEditToken ? 1 : 0.55,
              paddingHorizontal: 13,
              paddingVertical: 12,
            }}
            value={token}
          />
          {!draftMatchesActiveBaseUrl && serverUrl.trim() ? (
            <Text selectable style={{ color: theme.warning, fontSize: 11, lineHeight: 16 }}>
              Save this server URL before entering its API token.
            </Text>
          ) : null}
        </View>

        <Pressable
          accessibilityRole="button"
          disabled={!canTest || isTestingConnection}
          onPress={() => void saveAndTestConnection()}
          style={({ pressed }) => ({
            alignItems: 'center',
            backgroundColor: canTest ? theme.primary : theme.surfaceMuted,
            borderRadius: 14,
            opacity: pressed || isTestingConnection ? 0.72 : 1,
            padding: 13,
          })}
        >
          {isTestingConnection ? (
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
          <Pressable
            accessibilityRole="button"
            disabled={isTestingConnection}
            onPress={() => void forgetToken()}
          >
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

      {hasToken || displayedReadiness || readiness.error ? (
        <>
          <SectionHeading title="Control plane" />
          <SurfaceCard
            title="Readiness"
            trailing={
              <StatusPill
                label={displayedReadinessBadge.label}
                tone={displayedReadinessBadge.tone}
              />
            }
          >
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
              <Pressable
                accessibilityRole="button"
                disabled={readiness.isLoading || !hasToken}
                onPress={() => void readiness.refresh().catch(() => undefined)}
                style={({ pressed }) => ({
                  backgroundColor: theme.primarySoft,
                  borderColor: theme.primary,
                  borderRadius: 999,
                  borderWidth: 1,
                  opacity: readiness.isLoading || !hasToken ? 0.45 : pressed ? 0.72 : 1,
                  paddingHorizontal: 13,
                  paddingVertical: 8,
                })}
              >
                <Text style={{ color: theme.primary, fontSize: 12, fontWeight: '700' }}>
                  Refresh
                </Text>
              </Pressable>
              {readiness.isLoading ? <ActivityIndicator color={theme.primary} /> : null}
            </View>
            {readiness.error ? (
              <Text selectable style={{ color: theme.danger, fontSize: 13, lineHeight: 19 }}>
                {readiness.error.message}
              </Text>
            ) : null}
            {displayedReadiness ? (
              <ReadinessPanel readiness={displayedReadiness} />
            ) : (
              <Text selectable style={{ color: theme.textSecondary, fontSize: 13 }}>
                No readiness snapshot loaded.
              </Text>
            )}
          </SurfaceCard>
        </>
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
          <StatusPill
            label={
              inferenceHealthy === undefined
                ? 'Unknown'
                : inferenceHealthy
                  ? 'Ready'
                  : 'Unavailable'
            }
            tone={
              inferenceHealthy === undefined
                ? 'warning'
                : inferenceHealthy
                  ? 'positive'
                  : 'danger'
            }
          />
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 14 }}>
          <View style={{ flex: 1, gap: 4 }}>
            <Text selectable style={{ color: theme.text, fontSize: 16, fontWeight: '600' }}>
              Protected mode
            </Text>
            <Text selectable style={{ color: theme.textSecondary, fontSize: 13, lineHeight: 18 }}>
              Require approval before privileged executor actions.
            </Text>
          </View>
          <StatusPill
            label={
              executorRequiresApproval === undefined
                ? 'Unknown'
                : executorRequiresApproval
                  ? 'Approval required'
                  : 'Not required'
            }
            tone={
              executorRequiresApproval === undefined
                ? 'warning'
                : executorRequiresApproval
                  ? 'positive'
                  : 'danger'
            }
          />
        </View>
      </SurfaceCard>
    </ScreenScroll>
  );
}

function ReadinessPanel({ readiness }: { readiness: ReadinessResponse }) {
  return (
    <View style={{ gap: 9 }}>
      {readinessRows(readiness).map((row) => (
        <ReadinessRow key={row.key} row={row} />
      ))}
    </View>
  );
}

function ReadinessRow({ row }: { row: ReadinessRowSummary }) {
  const theme = useAppTheme();
  return (
    <View
      style={{
        alignItems: 'center',
        borderTopColor: theme.border,
        borderTopWidth: 1,
        flexDirection: 'row',
        gap: 12,
        paddingTop: 9,
      }}
    >
      <View style={{ flex: 1, gap: 2 }}>
        <Text selectable style={{ color: theme.text, fontSize: 14, fontWeight: '700' }}>
          {row.label}
        </Text>
        {row.detail ? (
          <Text selectable style={{ color: theme.textTertiary, fontSize: 11, lineHeight: 16 }}>
            {row.detail}
          </Text>
        ) : null}
      </View>
      <StatusPill label={row.value} tone={row.tone} />
    </View>
  );
}
