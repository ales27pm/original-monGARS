import { useState } from 'react';
import { ActivityIndicator, Pressable, Text, TextInput, View } from 'react-native';

import { ScreenScroll } from '@/components/screen-scroll';
import { SectionHeading } from '@/components/section-heading';
import { StatusPill } from '@/components/status-pill';
import { SurfaceCard } from '@/components/surface-card';
import { radii } from '@/constants/theme';
import { useAppTheme } from '@/hooks/use-app-theme';
import { useMemorySearch } from '@/hooks/use-mongars-api';
import { useMongars } from '@/providers/mongars-provider';

export default function MemoryScreen() {
  const { client, configurationError } = useMongars();
  const theme = useAppTheme();

  if (!client) {
    return (
      <ScreenScroll>
        <SurfaceCard tone="warning" title="Connect monGARS in Settings">
          <Text selectable style={{ color: theme.warning, fontSize: 14, lineHeight: 20 }}>
            {configurationError?.message ?? 'The local API address is not configured.'}
          </Text>
        </SurfaceCard>
      </ScreenScroll>
    );
  }

  return <ConnectedMemoryScreen />;
}

function ConnectedMemoryScreen() {
  const theme = useAppTheme();
  const search = useMemorySearch();
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<'hybrid' | 'semantic'>('hybrid');

  async function runSearch() {
    const normalized = query.trim();
    if (!normalized || search.isPending) return;
    try {
      await search.mutate({ query: normalized, mode, top_k: 12 });
    } catch {
      // The mutation exposes a user-readable error below the search controls.
    }
  }

  return (
    <ScreenScroll>
      <View style={{ flexDirection: 'row', gap: 8 }}>
        <TextInput
          accessibilityLabel="Search memory"
          maxLength={32_000}
          onChangeText={setQuery}
          onSubmitEditing={() => void runSearch()}
          placeholder="Search memory"
          placeholderTextColor={theme.textTertiary}
          returnKeyType="search"
          selectionColor={theme.primary}
          style={{
            backgroundColor: theme.input,
            borderCurve: 'continuous',
            borderRadius: radii.medium,
            color: theme.text,
            flex: 1,
            fontSize: 16,
            paddingHorizontal: 15,
            paddingVertical: 12,
          }}
          value={query}
        />
        <Pressable
          accessibilityRole="button"
          disabled={!query.trim() || search.isPending}
          onPress={() => void runSearch()}
          style={({ pressed }) => ({
            alignItems: 'center',
            backgroundColor: query.trim() ? theme.primary : theme.surfaceMuted,
            borderRadius: radii.medium,
            justifyContent: 'center',
            opacity: pressed ? 0.75 : 1,
            paddingHorizontal: 16,
          })}
        >
          {search.isPending ? (
            <ActivityIndicator color={theme.primaryContrast} />
          ) : (
            <Text style={{ color: theme.primaryContrast, fontSize: 13, fontWeight: '700' }}>
              Search
            </Text>
          )}
        </Pressable>
      </View>

      <View style={{ flexDirection: 'row', gap: 8 }}>
        {(['hybrid', 'semantic'] as const).map((option) => {
          const selected = mode === option;
          return (
            <Pressable
              accessibilityRole="button"
              key={option}
              onPress={() => setMode(option)}
              style={{
                backgroundColor: selected ? theme.primary : theme.surface,
                borderColor: selected ? theme.primary : theme.border,
                borderRadius: 999,
                borderWidth: 1,
                paddingHorizontal: 14,
                paddingVertical: 8,
              }}
            >
              <Text
                style={{
                  color: selected ? theme.primaryContrast : theme.textSecondary,
                  fontSize: 12,
                  fontWeight: '600',
                  textTransform: 'capitalize',
                }}
              >
                {option}
              </Text>
            </Pressable>
          );
        })}
      </View>

      {search.error ? (
        <SurfaceCard tone="danger" title="Memory search failed">
          <Text selectable style={{ color: theme.danger, fontSize: 13, lineHeight: 19 }}>
            {search.error.message}
          </Text>
        </SurfaceCard>
      ) : null}

      <SectionHeading
        detail={
          search.data
            ? `${search.data.hits.length} ${search.data.hits.length === 1 ? 'result' : 'results'}`
            : 'Searches stay inside your configured control plane'
        }
        title={search.data ? 'Results' : 'Hippocampus'}
      />

      {!search.data ? (
        <SurfaceCard tone="primary" title="Search durable memory">
          <Text selectable style={{ color: theme.textSecondary, fontSize: 14, lineHeight: 20 }}>
            Hybrid search combines semantic similarity with lexical matching and preserves source
            provenance for every hit.
          </Text>
        </SurfaceCard>
      ) : null}

      {search.data?.hits.map((hit, index) => (
        <SurfaceCard
          key={hit.chunk_id}
          eyebrow={`Result ${index + 1}`}
          title={hit.title ?? 'Untitled memory'}
          trailing={<StatusPill label={`${Math.round(hit.score * 100)}%`} tone="primary" />}
        >
          <Text selectable style={{ color: theme.textSecondary, fontSize: 14, lineHeight: 20 }}>
            {hit.text}
          </Text>
          <Text selectable style={{ color: theme.textTertiary, fontSize: 11 }}>
            {hit.source_uri ?? `Document ${hit.document_id.slice(0, 8)}`}
          </Text>
        </SurfaceCard>
      ))}

      {search.data && !search.data.hits.length ? (
        <SurfaceCard title="No matching memories">
          <Text selectable style={{ color: theme.textSecondary, fontSize: 14 }}>
            Try another phrase or switch search modes.
          </Text>
        </SurfaceCard>
      ) : null}
    </ScreenScroll>
  );
}
