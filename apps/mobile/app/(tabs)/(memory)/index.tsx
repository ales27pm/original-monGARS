import * as DocumentPicker from 'expo-document-picker';
import { useState } from 'react';
import { ActivityIndicator, Pressable, Text, TextInput, View } from 'react-native';

import { ScreenScroll } from '@/components/screen-scroll';
import { SectionHeading } from '@/components/section-heading';
import { StatusPill } from '@/components/status-pill';
import { SurfaceCard } from '@/components/surface-card';
import { radii } from '@/constants/theme';
import { useAppTheme } from '@/hooks/use-app-theme';
import { useDocumentUpload, useMemorySearch } from '@/hooks/use-mongars-api';
import { isAbortError } from '@/lib/api';
import {
  prepareDocumentUpload,
  SUPPORTED_DOCUMENT_MIME_TYPES,
  type PreparedDocumentUpload,
} from '@/lib/document-upload';
import { useMongars } from '@/providers/mongars-provider';
import type {
  DocumentRetentionClass,
  DocumentSensitivity,
} from '@/types/mongars-api';

const sensitivityOptions: readonly DocumentSensitivity[] = [
  'private',
  'shared',
  'restricted',
];
const retentionOptions: readonly DocumentRetentionClass[] = [
  'keep',
  'ttl_30d',
  'ttl_90d',
  'legal_hold',
];

function readableSize(bytes: number): string {
  if (bytes < 1_000_000) return `${Math.ceil(bytes / 1_000)} KB`;
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}

function optionLabel(value: DocumentSensitivity | DocumentRetentionClass): string {
  return value.replaceAll('_', ' ');
}

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
  const { hasToken, tokenStatus } = useMongars();
  const search = useMemorySearch();
  const upload = useDocumentUpload();
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<'hybrid' | 'semantic'>('hybrid');
  const [selectedDocument, setSelectedDocument] = useState<PreparedDocumentUpload | null>(null);
  const [title, setTitle] = useState('');
  const [sensitivity, setSensitivity] = useState<DocumentSensitivity>('private');
  const [retentionClass, setRetentionClass] = useState<DocumentRetentionClass>('keep');
  const [selectionError, setSelectionError] = useState<Error | null>(null);
  const [isPicking, setIsPicking] = useState(false);

  async function chooseDocument() {
    if (isPicking || upload.isPending) return;
    setIsPicking(true);
    setSelectionError(null);
    upload.reset();
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: [...SUPPORTED_DOCUMENT_MIME_TYPES],
        copyToCacheDirectory: true,
        multiple: false,
        base64: false,
      });
      if (result.canceled) return;
      const asset = result.assets[0];
      if (!asset) throw new Error('No document was returned by the picker.');
      setSelectedDocument(prepareDocumentUpload(asset));
    } catch (error) {
      setSelectedDocument(null);
      setSelectionError(
        error instanceof Error ? error : new Error('The selected document could not be opened.'),
      );
    } finally {
      setIsPicking(false);
    }
  }

  async function uploadDocument() {
    if (!selectedDocument || !hasToken || upload.isPending) return;
    try {
      await upload.mutate({
        file: selectedDocument.file,
        filename: selectedDocument.filename,
        declared_size: selectedDocument.size,
        source_timestamp: selectedDocument.sourceTimestamp,
        title: title.trim() || null,
        sensitivity,
        retention_class: retentionClass,
      });
    } catch (error) {
      if (isAbortError(error)) return;
      // The mutation exposes a user-readable error in the upload card.
    }
  }

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
      <SectionHeading
        detail="TXT, Markdown, HTML, PDF, or DOCX · 10 MB maximum"
        title="Import a document"
      />

      <SurfaceCard
        tone={upload.data ? 'positive' : 'default'}
        title={upload.data ? 'Approval required' : 'Main document ingestion'}
        trailing={
          upload.data ? (
            <StatusPill label="Waiting" tone="warning" />
          ) : selectedDocument ? (
            <StatusPill label={readableSize(selectedDocument.size)} tone="primary" />
          ) : null
        }
      >
        {upload.data ? (
          <>
            <Text selectable style={{ color: theme.positive, fontSize: 14, lineHeight: 20 }}>
              The exact ingestion metadata is queued for review. Open Tasks to inspect the action
              digest and approve parsing.
            </Text>
            <Text
              selectable
              style={{
                color: theme.textSecondary,
                fontFamily: process.env.EXPO_OS === 'ios' ? 'Menlo' : 'monospace',
                fontSize: 11,
                lineHeight: 17,
              }}
            >
              {upload.data.action_digest}
            </Text>
            <Pressable
              accessibilityRole="button"
              onPress={() => {
                upload.reset();
                setSelectedDocument(null);
                setTitle('');
              }}
              style={({ pressed }) => ({
                alignItems: 'center',
                backgroundColor: theme.surface,
                borderColor: theme.border,
                borderRadius: radii.medium,
                borderWidth: 1,
                opacity: pressed ? 0.72 : 1,
                paddingVertical: 11,
              })}
            >
              <Text style={{ color: theme.text, fontSize: 13, fontWeight: '700' }}>
                Import another document
              </Text>
            </Pressable>
          </>
        ) : (
          <>
            <Pressable
              accessibilityRole="button"
              disabled={isPicking || upload.isPending}
              onPress={() => void chooseDocument()}
              style={({ pressed }) => ({
                alignItems: 'center',
                backgroundColor: theme.primarySoft,
                borderColor: theme.primary,
                borderRadius: radii.medium,
                borderWidth: 1,
                opacity: isPicking || upload.isPending ? 0.55 : pressed ? 0.72 : 1,
                paddingVertical: 13,
              })}
            >
              {isPicking ? (
                <ActivityIndicator color={theme.primary} />
              ) : (
                <Text style={{ color: theme.primary, fontSize: 14, fontWeight: '700' }}>
                  {selectedDocument ? 'Choose a different document' : 'Choose document'}
                </Text>
              )}
            </Pressable>

            {selectedDocument ? (
              <View style={{ gap: 4 }}>
                <Text selectable style={{ color: theme.text, fontSize: 14, fontWeight: '700' }}>
                  {selectedDocument.filename}
                </Text>
                <Text selectable style={{ color: theme.textTertiary, fontSize: 11 }}>
                  {selectedDocument.mimeType} · {readableSize(selectedDocument.size)}
                </Text>
              </View>
            ) : (
              <Text selectable style={{ color: theme.textSecondary, fontSize: 13, lineHeight: 19 }}>
                The file stays local until you submit it. Parsing starts only after you review and
                approve the durable task.
              </Text>
            )}

            {selectedDocument ? (
              <>
                <View style={{ gap: 6 }}>
                  <Text style={{ color: theme.textSecondary, fontSize: 11, fontWeight: '700' }}>
                    OPTIONAL TITLE
                  </Text>
                  <TextInput
                    accessibilityLabel="Document title"
                    maxLength={500}
                    onChangeText={setTitle}
                    placeholder="Title for durable memory"
                    placeholderTextColor={theme.textTertiary}
                    selectionColor={theme.primary}
                    style={{
                      backgroundColor: theme.input,
                      borderRadius: radii.medium,
                      color: theme.text,
                      fontSize: 15,
                      paddingHorizontal: 14,
                      paddingVertical: 11,
                    }}
                    value={title}
                  />
                </View>

                <View style={{ gap: 7 }}>
                  <Text style={{ color: theme.textSecondary, fontSize: 11, fontWeight: '700' }}>
                    SENSITIVITY
                  </Text>
                  <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
                    {sensitivityOptions.map((option) => {
                      const selected = option === sensitivity;
                      return (
                        <Pressable
                          accessibilityRole="button"
                          key={option}
                          onPress={() => setSensitivity(option)}
                          style={{
                            backgroundColor: selected ? theme.primary : theme.surface,
                            borderColor: selected ? theme.primary : theme.border,
                            borderRadius: 999,
                            borderWidth: 1,
                            paddingHorizontal: 12,
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
                            {optionLabel(option)}
                          </Text>
                        </Pressable>
                      );
                    })}
                  </View>
                </View>

                <View style={{ gap: 7 }}>
                  <Text style={{ color: theme.textSecondary, fontSize: 11, fontWeight: '700' }}>
                    RETENTION
                  </Text>
                  <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
                    {retentionOptions.map((option) => {
                      const selected = option === retentionClass;
                      return (
                        <Pressable
                          accessibilityRole="button"
                          key={option}
                          onPress={() => setRetentionClass(option)}
                          style={{
                            backgroundColor: selected ? theme.primary : theme.surface,
                            borderColor: selected ? theme.primary : theme.border,
                            borderRadius: 999,
                            borderWidth: 1,
                            paddingHorizontal: 12,
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
                            {optionLabel(option)}
                          </Text>
                        </Pressable>
                      );
                    })}
                  </View>
                </View>

                {!hasToken ? (
                  <Text selectable style={{ color: theme.warning, fontSize: 12, lineHeight: 18 }}>
                    {tokenStatus === 'loading'
                      ? 'Checking the saved API token…'
                      : 'Save this server’s API token in Settings before uploading.'}
                  </Text>
                ) : null}

                {upload.isPending ? (
                  <View
                    accessibilityLiveRegion="polite"
                    style={{ alignItems: 'center', flexDirection: 'row', gap: 10 }}
                  >
                    <ActivityIndicator color={theme.primary} />
                    <Text style={{ color: theme.textSecondary, flex: 1, fontSize: 13 }}>
                      Uploading {selectedDocument.filename} securely…
                    </Text>
                    <Pressable accessibilityRole="button" onPress={upload.cancel}>
                      <Text style={{ color: theme.danger, fontSize: 13, fontWeight: '700' }}>
                        Cancel
                      </Text>
                    </Pressable>
                  </View>
                ) : (
                  <Pressable
                    accessibilityRole="button"
                    disabled={!hasToken}
                    onPress={() => void uploadDocument()}
                    style={({ pressed }) => ({
                      alignItems: 'center',
                      backgroundColor: hasToken ? theme.primary : theme.surfaceMuted,
                      borderRadius: radii.medium,
                      opacity: pressed ? 0.75 : 1,
                      paddingVertical: 13,
                    })}
                  >
                    <Text
                      style={{
                        color: hasToken ? theme.primaryContrast : theme.textTertiary,
                        fontSize: 14,
                        fontWeight: '700',
                      }}
                    >
                      Upload for approval
                    </Text>
                  </Pressable>
                )}
              </>
            ) : null}
          </>
        )}

        {selectionError || upload.error ? (
          <Text selectable style={{ color: theme.danger, fontSize: 12, lineHeight: 18 }}>
            {(upload.error ?? selectionError)?.message}
          </Text>
        ) : null}
      </SurfaceCard>

      <SectionHeading detail="Semantic and lexical retrieval" title="Search memory" />

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
