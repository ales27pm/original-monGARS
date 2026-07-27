import { useState } from 'react';
import { Text, TextInput, View } from 'react-native';

import { AppButton } from '@/components/app-button';
import { AppIcon } from '@/components/app-icon';
import { ScreenScroll } from '@/components/screen-scroll';
import { SectionHeading } from '@/components/section-heading';
import { StatusPill } from '@/components/status-pill';
import { SurfaceCard } from '@/components/surface-card';
import { VisualAsset } from '@/components/visual-asset';
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
  const credentialTransportAllowed = transportSecurity?.canSendCredentials === true;
  const serverUrl = hasServerUrlEdits ? serverUrlInput : baseUrl ?? '';
  const draftMatchesActiveBaseUrl = isActiveMongarsApiBaseUrlDraft(serverUrl, baseUrl);
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

  async function saveServerUrl() {
    if (!serverUrl.trim() || serverUrlSaving) return;
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
      <SectionHeading level="screen" title="Settings" />
      <Text style={{ color: theme.primary, fontSize: 10, fontWeight: '700' }}>LOCAL CORTEX</Text>
      <SurfaceCard>
        <View style={{ alignItems: 'center', flexDirection: 'row', gap: 11 }}>
          <VisualAsset
            accessibilityLabel="Local cortex security emblem"
            name="cortexEmblem"
            size={48}
          />
          <View style={{ flex: 1, gap: 3 }}>
            <Text selectable style={{ color: theme.text, fontSize: 14, fontWeight: '700' }}>
              Private local model
            </Text>
            <Text
              ellipsizeMode="middle"
              numberOfLines={1}
              selectable
              style={{ color: theme.textSecondary, fontSize: 10 }}
            >
              {baseUrl ?? 'No server configured'}
            </Text>
          </View>
          <View style={{ alignItems: 'flex-end', gap: 4 }}>
            <Text style={{ color: theme.textTertiary, fontSize: 9 }}>READINESS</Text>
            <StatusPill
              label={
                connectionState === 'ready'
                  ? 'Ready'
                  : hasToken
                    ? displayedReadinessBadge.label
                    : 'Setup'
              }
              tone={
                connectionState === 'ready'
                  ? 'positive'
                  : hasToken
                    ? displayedReadinessBadge.tone
                    : 'warning'
              }
            />
          </View>
          <AppIcon color={theme.textTertiary} name="chevronRight" size={17} />
        </View>
      </SurfaceCard>

      <SectionHeading
        detail="The HTTPS server and token remain on this device."
        title="Connections"
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
              fontSize: 13,
              paddingHorizontal: 12,
              paddingVertical: 10,
            }}
            value={serverUrl}
          />
          <AppButton
            disabled={!serverUrl.trim() || serverUrlSaving}
            fullWidth
            label="Save server URL"
            loading={serverUrlSaving}
            onPress={() => void saveServerUrl()}
            variant="soft"
          />
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
            editable={credentialTransportAllowed && draftMatchesActiveBaseUrl}
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
              fontSize: 13,
              opacity: credentialTransportAllowed && draftMatchesActiveBaseUrl ? 1 : 0.55,
              paddingHorizontal: 12,
              paddingVertical: 10,
            }}
            value={token}
          />
          {!draftMatchesActiveBaseUrl && serverUrl.trim() ? (
            <Text selectable style={{ color: theme.warning, fontSize: 11, lineHeight: 16 }}>
              Save this server URL before entering its API token.
            </Text>
          ) : null}
        </View>

        <AppButton
          disabled={!canTest || connectionState === 'testing'}
          fullWidth
          label={token.trim() ? 'Save and test connection' : 'Test saved connection'}
          loading={connectionState === 'testing'}
          onPress={() => void saveAndTestConnection()}
        />

        {hasToken ? (
          <AppButton
            fullWidth
            label="Forget saved token"
            onPress={() => void forgetToken()}
            tone="danger"
            variant="soft"
          />
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
              <AppButton
                disabled={readiness.isLoading || !hasToken}
                label="Refresh"
                loading={readiness.isLoading}
                onPress={() => void readiness.refresh().catch(() => undefined)}
                size="compact"
                variant="soft"
              />
            </View>
            {readiness.error ? (
              <Text selectable style={{ color: theme.danger, fontSize: 13, lineHeight: 19 }}>
                {readiness.error.message}
              </Text>
            ) : null}
            {displayedReadiness ? (
              <ReadinessPanel readiness={displayedReadiness} />
            ) : (
              <View style={{ alignItems: 'center', flexDirection: 'row', gap: 14 }}>
                <VisualAsset
                  accessibilityLabel="Readiness security visual"
                  name="readinessSecurity"
                  size={76}
                />
                <Text
                  selectable
                  style={{ color: theme.textSecondary, flex: 1, fontSize: 13, lineHeight: 19 }}
                >
                  No readiness snapshot loaded.
                </Text>
              </View>
            )}
          </SurfaceCard>
        </>
      ) : null}

      <SectionHeading detail="Protected defaults for local work." title="Security" />
      <SurfaceCard>
        <View style={{ alignItems: 'center', flexDirection: 'row', gap: 11 }}>
          <AppIcon color={theme.textSecondary} name="shield" size={20} />
          <View style={{ flex: 1, gap: 2 }}>
            <Text selectable style={{ color: theme.text, fontSize: 13, fontWeight: '700' }}>
              Require local inference
            </Text>
            <Text selectable style={{ color: theme.textSecondary, fontSize: 11, lineHeight: 16 }}>
              Prevent requests from using a remote fallback endpoint.
            </Text>
          </View>
          <StatusPill label="Required" tone="positive" />
        </View>
        <View
          style={{
            alignItems: 'center',
            borderTopColor: theme.border,
            borderTopWidth: 1,
            flexDirection: 'row',
            gap: 11,
            paddingTop: 10,
          }}
        >
          <AppIcon color={theme.textSecondary} name="lock" size={20} />
          <View style={{ flex: 1, gap: 2 }}>
            <Text selectable style={{ color: theme.text, fontSize: 13, fontWeight: '700' }}>
              Protected mode
            </Text>
            <Text selectable style={{ color: theme.textSecondary, fontSize: 11, lineHeight: 16 }}>
              High-risk effects require exact-action approval.
            </Text>
          </View>
          <StatusPill label="On" tone="primary" />
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
