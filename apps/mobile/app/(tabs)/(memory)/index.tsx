import * as DocumentPicker from 'expo-document-picker';
import { useState } from 'react';
import { ActivityIndicator, Pressable, Text, TextInput, View } from 'react-native';

import { AppButton } from '@/components/app-button';
import { AppIcon } from '@/components/app-icon';
import { IconButton } from '@/components/icon-button';
import { ScreenScroll } from '@/components/screen-scroll';
import { SectionHeading } from '@/components/section-heading';
import {
  SegmentedControl,
  type SegmentedControlOption,
} from '@/components/segmented-control';
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

const sensitivityControlOptions: readonly SegmentedControlOption<DocumentSensitivity>[] =
  sensitivityOptions.map((option) => ({ label: optionLabel(option), value: option }));
const retentionControlOptions: readonly SegmentedControlOption<DocumentRetentionClass>[] =
  retentionOptions.map((option) => ({ label: optionLabel(option), value: option }));
const searchModeOptions: readonly SegmentedControlOption<'hybrid' | 'semantic'>[] = [
  { label: 'Hybrid', value: 'hybrid' },
  { label: 'Semantic', value: 'semantic' },
];
type MemoryView = 'documents' | 'search';
const memoryViewOptions: readonly SegmentedControlOption<MemoryView>[] = [
  { label: 'Documents', value: 'documents' },
  { label: 'Search', value: 'search' },
];

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
  const [view, setView] = useState<MemoryView>('documents');
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
      <SectionHeading level="screen" title="Memory" />
      <SegmentedControl
        accessibilityLabel="Memory view"
        appearance="tabs"
        onChange={setView}
        options={memoryViewOptions}
        value={view}
      />

      {view === 'documents' ? (
        <>
          {upload.data ? (
            <SurfaceCard
              tone="positive"
              title="Approval required"
              trailing={<StatusPill label="Waiting" tone="warning" />}
            >
              <Text selectable style={{ color: theme.positive, fontSize: 13, lineHeight: 18 }}>
                The exact ingestion metadata is queued for protected review in Tasks.
              </Text>
              <Text
                selectable
                style={{
                  color: theme.textSecondary,
                  fontFamily: process.env.EXPO_OS === 'ios' ? 'Menlo' : 'monospace',
                  fontSize: 10,
                  lineHeight: 15,
                }}
              >
                {upload.data.action_digest}
              </Text>
              <AppButton
                fullWidth
                label="Import another document"
                onPress={() => {
                  upload.reset();
                  setSelectedDocument(null);
                  setTitle('');
                }}
                tone="neutral"
                variant="outline"
              />
            </SurfaceCard>
          ) : (
            <SurfaceCard title="Import a document">
              <Pressable
                accessibilityLabel="Import a document"
                accessibilityRole="button"
                disabled={isPicking || upload.isPending}
                onPress={() => void chooseDocument()}
                style={({ pressed }) => ({
                  alignItems: 'center',
                  backgroundColor: pressed ? theme.primarySoft : theme.surface,
                  borderColor: theme.primary,
                  borderRadius: radii.large,
                  borderStyle: 'dashed',
                  borderWidth: 1,
                  gap: 5,
                  opacity: isPicking || upload.isPending ? 0.5 : 1,
                  paddingHorizontal: 14,
                  paddingVertical: 18,
                })}
              >
                {isPicking ? (
                  <ActivityIndicator color={theme.primary} />
                ) : (
                  <AppIcon color={theme.primary} name="upload" size={30} />
                )}
                <Text style={{ color: theme.primary, fontSize: 13, fontWeight: '700' }}>
                  {selectedDocument ? 'Choose a different document' : 'Choose document'}
                </Text>
                <Text style={{ color: theme.textTertiary, fontSize: 10 }}>
                  PDF, DOCX, TXT, Markdown, or HTML · 10 MB maximum
                </Text>
              </Pressable>

              {selectedDocument ? (
                <>
                  <View style={{ gap: 3 }}>
                    <Text selectable style={{ color: theme.text, fontSize: 13, fontWeight: '700' }}>
                      {selectedDocument.filename}
                    </Text>
                    <Text selectable style={{ color: theme.textTertiary, fontSize: 10 }}>
                      {selectedDocument.mimeType} · {readableSize(selectedDocument.size)}
                    </Text>
                  </View>
                  <View style={{ gap: 5 }}>
                    <Text style={{ color: theme.textSecondary, fontSize: 10, fontWeight: '700' }}>
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
                        fontSize: 14,
                        paddingHorizontal: 12,
                        paddingVertical: 10,
                      }}
                      value={title}
                    />
                  </View>
                  <View style={{ gap: 6 }}>
                    <Text style={{ color: theme.textSecondary, fontSize: 10, fontWeight: '700' }}>
                      SENSITIVITY
                    </Text>
                    <SegmentedControl
                      accessibilityLabel="Document sensitivity"
                      fill={false}
                      onChange={setSensitivity}
                      options={sensitivityControlOptions}
                      size="compact"
                      value={sensitivity}
                      wrap
                    />
                  </View>
                  <View style={{ gap: 6 }}>
                    <Text style={{ color: theme.textSecondary, fontSize: 10, fontWeight: '700' }}>
                      RETENTION
                    </Text>
                    <SegmentedControl
                      accessibilityLabel="Document retention"
                      fill={false}
                      onChange={setRetentionClass}
                      options={retentionControlOptions}
                      size="compact"
                      value={retentionClass}
                      wrap
                    />
                  </View>
                  {!hasToken ? (
                    <Text selectable style={{ color: theme.warning, fontSize: 11, lineHeight: 16 }}>
                      {tokenStatus === 'loading'
                        ? 'Checking the saved API token…'
                        : 'Save this server’s API token in Settings before uploading.'}
                    </Text>
                  ) : null}
                  {upload.isPending ? (
                    <View
                      accessibilityLiveRegion="polite"
                      style={{ alignItems: 'center', flexDirection: 'row', gap: 9 }}
                    >
                      <ActivityIndicator color={theme.primary} />
                      <Text style={{ color: theme.textSecondary, flex: 1, fontSize: 12 }}>
                        Uploading {selectedDocument.filename} securely…
                      </Text>
                      <AppButton
                        label="Cancel"
                        onPress={upload.cancel}
                        size="compact"
                        tone="danger"
                        variant="soft"
                      />
                    </View>
                  ) : (
                    <AppButton
                      disabled={!hasToken}
                      fullWidth
                      label="Upload for approval"
                      onPress={() => void uploadDocument()}
                    />
                  )}
                </>
              ) : null}
              {selectionError || upload.error ? (
                <Text selectable style={{ color: theme.danger, fontSize: 11, lineHeight: 16 }}>
                  {(upload.error ?? selectionError)?.message}
                </Text>
              ) : null}
            </SurfaceCard>
          )}

          <SectionHeading title="Local ingestion" />
          <SurfaceCard>
            <View style={{ alignItems: 'center', flexDirection: 'row', gap: 11 }}>
              <AppIcon color={theme.primary} name="lock" size={21} />
              <View style={{ flex: 1, gap: 2 }}>
                <Text style={{ color: theme.text, fontSize: 13, fontWeight: '700' }}>
                  Protected by approval
                </Text>
                <Text style={{ color: theme.textSecondary, fontSize: 11, lineHeight: 16 }}>
                  Files remain local. Parsing starts only after the durable task is reviewed.
                </Text>
              </View>
              <AppIcon color={theme.textTertiary} name="chevronRight" size={17} />
            </View>
          </SurfaceCard>
        </>
      ) : (
        <>
          <View style={{ alignItems: 'center', flexDirection: 'row', gap: 8 }}>
            <View
              style={{
                alignItems: 'center',
                backgroundColor: theme.input,
                borderRadius: radii.medium,
                flex: 1,
                flexDirection: 'row',
                gap: 8,
                paddingHorizontal: 12,
              }}
            >
              <AppIcon color={theme.textTertiary} name="search" size={18} />
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
                  color: theme.text,
                  flex: 1,
                  fontSize: 14,
                  paddingVertical: 11,
                }}
                value={query}
              />
            </View>
            <IconButton
              accessibilityLabel="Search memory"
              disabled={!query.trim() || search.isPending}
              icon="search"
              onPress={() => void runSearch()}
              tone="primary"
              variant="solid"
            />
          </View>
          <SegmentedControl
            accessibilityLabel="Memory search mode"
            fill={false}
            onChange={setMode}
            options={searchModeOptions}
            size="compact"
            value={mode}
          />
          {search.error ? (
            <SurfaceCard tone="danger" title="Memory search failed">
              <Text selectable style={{ color: theme.danger, fontSize: 12, lineHeight: 17 }}>
                {search.error.message}
              </Text>
            </SurfaceCard>
          ) : null}
          <SectionHeading
            detail={
              search.data
                ? `${search.data.hits.length} ${search.data.hits.length === 1 ? 'result' : 'results'}`
                : 'Semantic and lexical retrieval with source provenance'
            }
            title={search.data ? 'Results' : 'Search durable memory'}
          />
          {!search.data ? (
            <SurfaceCard>
              <Text selectable style={{ color: theme.textSecondary, fontSize: 12, lineHeight: 17 }}>
                Searches stay inside your configured control plane and preserve the source of every
                retrieved passage.
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
              <Text selectable style={{ color: theme.textSecondary, fontSize: 13, lineHeight: 18 }}>
                {hit.text}
              </Text>
              <Text selectable style={{ color: theme.textTertiary, fontSize: 10 }}>
                {hit.source_uri ?? `Document ${hit.document_id.slice(0, 8)}`}
              </Text>
            </SurfaceCard>
          ))}
          {search.data && !search.data.hits.length ? (
            <SurfaceCard title="No matching memories">
              <Text selectable style={{ color: theme.textSecondary, fontSize: 12 }}>
                Try another phrase or switch search modes.
              </Text>
            </SurfaceCard>
          ) : null}
        </>
      )}
    </ScreenScroll>
  );
}
