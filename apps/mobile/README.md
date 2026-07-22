# monGARS Mobile

Expo SDK 54 / Expo Go client for the local monGARS control plane. It includes chat, memory, task,
and connection settings surfaces.

## Run on an iPhone with Expo Go

Start monGARS on the workstation, then install the mobile dependencies:

```bash
cd apps/mobile
cp .env.example .env.local
npm install
npm run start:lan
```

The build-time URL is optional. If you use one for development, configure a trusted HTTPS origin:

```dotenv
EXPO_PUBLIC_MONGARS_API_URL=https://YOUR_MONGARS_HOSTNAME
```

Scan the Metro QR code with Expo Go. Open Settings, save the server URL, and paste the API token when
prompted. Native builds remember the selected URL, so production/TestFlight binaries do not require
a build-time server address.
The token and its normalized server origin are stored together through `expo-secure-store` in the
iOS Keychain; a credential is never sent to a different origin and must never be placed in an
`EXPO_PUBLIC_` environment variable or committed to the repository. The browser preview keeps a
token in memory only and loses it on refresh.

Plain LAN HTTP can be used for the unauthenticated health/readiness checks only. The client refuses
to save or send a bearer token to non-loopback HTTP, so configure a trusted HTTPS endpoint before
using chat, memory, or tasks from an iPhone.

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

## Verification

```bash
npm run typecheck
npm run lint
npm test
```

## App Store Connect / TestFlight

The production EAS profile uses the Expo SDK 54 Xcode 26 image, remote build-number management,
and store distribution. It intentionally ships without a fixed monGARS server URL; users select an
HTTPS endpoint in Settings after installation.

```bash
npx eas-cli@latest build --platform ios --profile production
npx eas-cli@latest submit --platform ios --profile production --latest
```

The iOS config declares OS-provided encryption as exempt and aggregates the required-reason API
entries shipped by the current Expo and React Native dependencies. Re-check the final Apple
processing report whenever those native dependencies change.
