# monGARS Mobile

Expo SDK 54 development-client app for the local monGARS control plane. It includes chat, memory,
task, document ingestion, and connection settings surfaces.

> **Expo Go is not supported.** Install the pinned monGARS development client before opening a
> Metro QR code. The standard Expo Go app cannot load this runtime.

## Run on an iPhone with the development client

Start monGARS on the workstation, install the mobile dependencies, and sign in to EAS:

```bash
cd apps/mobile
cp .env.example .env.local
npm ci
npx --yes eas-cli@21.0.3 login
```

The build-time URL is optional. If you use one for development, configure a trusted HTTPS origin:

```dotenv
EXPO_PUBLIC_MONGARS_API_URL=https://YOUR_MONGARS_HOSTNAME
```

Create the pinned SDK 54 development client, install it from the EAS internal-distribution link, and
then start Metro:

```bash
npm run build:development:ios
npm run start:dev-client:lan
```

Register the iPhone with EAS if prompted during the first internal iOS build. Open the installed
**monGARS** development client—not Expo Go—and scan the Metro QR code. Open Settings, save the
server URL, and paste the API token when prompted. Native builds remember the selected URL, so
production/TestFlight binaries do not require a build-time server address.
The token and its normalized server origin are stored together through `expo-secure-store` in the
iOS Keychain; a credential is never sent to a different origin and must never be placed in an
`EXPO_PUBLIC_` environment variable or committed to the repository. The browser preview keeps a
token in memory only and loses it on refresh.

Plain LAN HTTP can be used only for the unauthenticated liveness check. Detailed readiness requires
the bearer token, and the client refuses to save or send that credential to non-loopback HTTP, so
configure a trusted HTTPS endpoint before inspecting readiness or using chat, memory, or tasks from
an iPhone.

## API layer

`MongarsProvider` owns API configuration and token state:

```tsx
import { MongarsProvider } from '@/providers/mongars-provider';

export function Root() {
  return <MongarsProvider>{/* routes */}</MongarsProvider>;
}
```

UI code can use the provider and abortable request hooks:

```tsx
import { useChat, useReadiness } from '@/hooks/use-mongars-api';
import { useMongars } from '@/providers/mongars-provider';

const { baseUrl, hasToken, saveToken, clearToken } = useMongars();
const readiness = useReadiness();
const chat = useChat();

await chat.mutate({
  message: 'Summarize my latest memory.',
  require_local_only: true,
  web_search: 'off',
});
```

Available hooks:

- `useReadiness({ auto? })`
- `useTasks({ auto?, limit? })`
- `useTaskDetail(taskId, { auto? })` for the bounded payload summary and action digest
- `useTaskPayloadPage(taskId, page, digest, pageCount, pageSize, { auto? })` for one exact page
- `useChat()`
- `useMemorySearch()`
- `useCreateMemoryNote()`
- `useDocumentUpload()`
- `useApproveTask()`
- `useCancelTask()`

Every mutation exposes `mutate`, `cancel`, `reset`, `data`, `error`, and `isPending`. Queries expose
`refresh`, `cancel`, `data`, `error`, and `isLoading`. Low-level typed methods and `AbortSignal`
support are available through `MongarsClient` from `@/lib/api`.

HTTP failures use the typed `ApiError` shape (`status`, `code`, and `detail`). A server `401`
automatically clears the rejected token from the current session and SecureStore.

Chat requests always include the selected web-search policy (`Off`, `Auto`, or `Required`). Task
approval renders a server-bounded payload preview by default; exact content is downloaded through
one fixed-size page request at a time. Approval sends only the reviewed action digest, while the
server validates that digest against the complete canonical payload.

The Memory screen accepts one TXT, Markdown, HTML, PDF, or DOCX document up to 10 MB. The picker
copies the selected file to the app cache, and `expo/fetch` streams that file in a multipart request
without base64 conversion. The upload includes its measured size, source timestamp, sensitivity,
and retention policy, then waits for exact task review before any parsing begins.

## Verification

```bash
npm run typecheck
npm run lint
npm test
npx --yes expo-doctor@latest
```

The development profile is reproducible: `expo-dev-client` is held on the Expo SDK 54-compatible
6.0.x line, EAS CLI is pinned to 21.0.3, and the iOS builder uses the `sdk-54` image.

## App Store Connect / TestFlight

The production EAS profile uses the Expo SDK 54 Xcode 26 image, remote build-number management,
and store distribution. It intentionally ships without a fixed monGARS server URL; users select an
HTTPS endpoint in Settings after installation.

```bash
npx --yes eas-cli@21.0.3 build --platform ios --profile production
npx --yes eas-cli@21.0.3 submit --platform ios --profile production --latest
```

The iOS config declares OS-provided encryption as exempt and aggregates the required-reason API
entries shipped by the current Expo and React Native dependencies. Re-check the final Apple
processing report whenever those native dependencies change.
