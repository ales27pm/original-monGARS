export type VoiceLoopState =
  | 'idle'
  | 'requesting_permission'
  | 'listening'
  | 'finalizing'
  | 'thinking'
  | 'speaking';

export type VoiceLoopEvent =
  | 'start_push_to_talk'
  | 'permission_granted'
  | 'permission_denied'
  | 'stop_recording'
  | 'silence_timeout'
  | 'recording_failed'
  | 'transcription_complete'
  | 'transcription_rejected'
  | 'speak_complete'
  | 'tts_stopped'
  | 'interruption'
  | 'cancel'
  | 'network_lost'
  | 'auto_restart';

export type VoiceLoopTransitionError = {
  state: VoiceLoopState;
  event: VoiceLoopEvent;
  reason: string;
};

const NEXT_STATE: Record<VoiceLoopState, Record<VoiceLoopEvent, VoiceLoopState | null>> = {
  idle: {
    start_push_to_talk: 'requesting_permission',
    permission_granted: null,
    permission_denied: null,
    stop_recording: null,
    silence_timeout: null,
    recording_failed: null,
    transcription_complete: null,
    transcription_rejected: null,
    speak_complete: null,
    tts_stopped: null,
    interruption: null,
    cancel: null,
    network_lost: null,
  },
  requesting_permission: {
    permission_granted: 'listening',
    permission_denied: 'idle',
    cancel: 'idle',
    start_push_to_talk: null,
    stop_recording: 'idle',
    silence_timeout: null,
    recording_failed: null,
    transcription_complete: null,
    transcription_rejected: null,
    speak_complete: null,
    tts_stopped: null,
    interruption: null,
    network_lost: 'idle',
  },
  listening: {
    stop_recording: 'finalizing',
    silence_timeout: 'finalizing',
    recording_failed: 'idle',
    cancel: 'idle',
    network_lost: 'idle',
    start_push_to_talk: null,
    permission_granted: null,
    permission_denied: null,
    transcription_complete: null,
    transcription_rejected: null,
    speak_complete: null,
    tts_stopped: null,
    interruption: 'idle',
  },
  finalizing: {
    transcription_complete: 'thinking',
    auto_restart: null,
    transcription_rejected: 'idle',
    recording_failed: 'idle',
    cancel: 'idle',
    start_push_to_talk: null,
    permission_granted: null,
    permission_denied: null,
    stop_recording: null,
    silence_timeout: null,
    speak_complete: null,
    tts_stopped: null,
    interruption: 'idle',
    network_lost: 'idle',
  },
  thinking: {
    transcription_complete: null,
    transcription_rejected: null,
    speak_complete: 'speaking',
    recording_failed: 'idle',
    cancel: 'idle',
    start_push_to_talk: null,
    permission_granted: null,
    permission_denied: null,
    stop_recording: null,
    silence_timeout: null,
    network_lost: 'idle',
    tts_stopped: null,
    interruption: 'cancel',
  },
  speaking: {
    speak_complete: 'idle',
    auto_restart: 'listening',
    tts_stopped: 'idle',
    interruption: 'idle',
    cancel: 'idle',
    network_lost: 'idle',
    start_push_to_talk: null,
    permission_granted: null,
    permission_denied: null,
    stop_recording: null,
    silence_timeout: null,
    recording_failed: null,
    transcription_complete: null,
    transcription_rejected: null,
  },
};

function makeTransitionError(state: VoiceLoopState, event: VoiceLoopEvent): VoiceLoopTransitionError {
  return {
    state,
    event,
    reason: `invalid ${state} -> ${event}`,
  };
}

export function nextVoiceState(state: VoiceLoopState, event: VoiceLoopEvent): VoiceLoopState {
  const next = NEXT_STATE[state]?.[event];
  if (next === null) {
    throw makeTransitionError(state, event);
  }
  return next;
}

export function canTransition(state: VoiceLoopState, event: VoiceLoopEvent): boolean {
  return NEXT_STATE[state]?.[event] !== null;
}

export function nextLabel(state: VoiceLoopState): string {
  switch (state) {
    case 'idle':
      return 'Ready';
    case 'requesting_permission':
      return 'Requesting microphone permission';
    case 'listening':
      return 'Listening';
    case 'finalizing':
      return 'Finalizing recording';
    case 'thinking':
      return 'Thinking';
    case 'speaking':
      return 'Speaking';
    default:
      return 'Unknown';
  }
}
