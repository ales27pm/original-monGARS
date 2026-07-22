declare namespace NodeJS {
  interface ProcessEnv {
    /** Public server location only. Never place a bearer token in an Expo environment variable. */
    EXPO_PUBLIC_MONGARS_API_URL?: string;
  }
}
