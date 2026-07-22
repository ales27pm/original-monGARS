import type { DocumentPickerAsset } from 'expo-document-picker';
import { File } from 'expo-file-system';

export const MAX_DOCUMENT_UPLOAD_BYTES = 10_000_000;

export const SUPPORTED_DOCUMENT_MIME_TYPES = [
  'text/plain',
  'text/markdown',
  'text/html',
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
] as const;

export type SupportedDocumentMimeType = (typeof SUPPORTED_DOCUMENT_MIME_TYPES)[number];

export type PreparedDocumentUpload = {
  file: Blob;
  filename: string;
  mimeType: SupportedDocumentMimeType;
  size: number;
  sourceTimestamp: string;
};

const MIME_BY_EXTENSION: Readonly<Record<string, SupportedDocumentMimeType>> = {
  '.txt': 'text/plain',
  '.md': 'text/markdown',
  '.markdown': 'text/markdown',
  '.html': 'text/html',
  '.htm': 'text/html',
  '.pdf': 'application/pdf',
  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
};

const GENERIC_MIME_TYPES = new Set(['', 'application/octet-stream', 'binary/octet-stream']);

export class DocumentSelectionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'DocumentSelectionError';
  }
}

function expectedMimeType(filename: string): SupportedDocumentMimeType | null {
  const dotIndex = filename.lastIndexOf('.');
  if (dotIndex < 0) return null;
  return MIME_BY_EXTENSION[filename.slice(dotIndex).toLowerCase()] ?? null;
}

function normalizedMimeType(value: string | undefined): string {
  return value?.split(';', 1)[0]?.trim().toLowerCase() ?? '';
}

function safeFilename(filename: string): string {
  const normalized = filename.normalize('NFC');
  if (
    !normalized ||
    normalized !== normalized.trim() ||
    normalized.length > 255 ||
    normalized === '.' ||
    normalized === '..' ||
    normalized.includes('/') ||
    normalized.includes('\\') ||
    /[\u2044\u2215\u29f5\u29f8\uff0f\uff3c]/u.test(normalized) ||
    /\p{Cf}/u.test(normalized) ||
    /[\u0000-\u001f\u007f]/u.test(normalized)
  ) {
    throw new DocumentSelectionError('Choose a document with a safe filename.');
  }
  return normalized;
}

export function prepareDocumentUpload(asset: DocumentPickerAsset): PreparedDocumentUpload {
  const filename = safeFilename(asset.name);
  const expectedType = expectedMimeType(filename);
  if (!expectedType) {
    throw new DocumentSelectionError('Choose a TXT, Markdown, HTML, PDF, or DOCX document.');
  }

  const declaredType = normalizedMimeType(asset.mimeType);
  if (
    !GENERIC_MIME_TYPES.has(declaredType) &&
    declaredType !== expectedType
  ) {
    throw new DocumentSelectionError(
      'The selected document type does not match its filename extension.',
    );
  }

  // DocumentPicker supplies a native File on web. On iOS and Android, expo-file-system creates a
  // streaming Blob over the cache URI; neither path reads or base64-encodes the document in JS.
  const sourceFile: Blob = asset.file ?? new File(asset.uri);
  const measuredSize = sourceFile.size;
  if (
    !Number.isSafeInteger(measuredSize) ||
    measuredSize <= 0 ||
    measuredSize > MAX_DOCUMENT_UPLOAD_BYTES
  ) {
    throw new DocumentSelectionError('Choose a non-empty document no larger than 10 MB.');
  }
  if (
    asset.size !== undefined &&
    Number.isSafeInteger(asset.size) &&
    asset.size > 0 &&
    asset.size !== measuredSize
  ) {
    throw new DocumentSelectionError('The selected document changed while it was being prepared.');
  }

  const sourceDate = new Date(asset.lastModified);
  const sourceTimestamp = Number.isFinite(sourceDate.getTime())
    ? sourceDate.toISOString()
    : new Date().toISOString();

  return {
    file: sourceFile.slice(0, measuredSize, expectedType),
    filename,
    mimeType: expectedType,
    size: measuredSize,
    sourceTimestamp,
  };
}
